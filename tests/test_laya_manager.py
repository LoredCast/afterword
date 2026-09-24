"""Managed Laya lifecycle, tested against real processes.

A real virtualenv is created and a tiny stand-in ``laya`` package (standard
library only) is placed in it. It speaks the same HTTP protocol as
``laya.serve``, so start-up, health checks, authentication, crash recovery,
stopping and removal all run for real, without downloading torch or a model.

The full dashboard install (pip, wheel, first start) is exercised by
test_install_from_dashboard when AFTERWORD_TEST_NETWORK=1, because it needs to
reach PyPI to update pip.
"""
import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

from afterword import laya_manager

from helpers import Client, cleanup, make_app, post_comment

FAKE_SERVE = r'''
import json, os, http.server

HOST = os.environ.get("LAYA_HOST", "0.0.0.0")
PORT = int(os.environ.get("LAYA_PORT", "8000"))
KEY = os.environ.get("LAYA_API_KEY")

with open("seen-env.json", "w") as fh:
    json.dump(dict(os.environ), fh)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.send(200, {"status": "ok", "loaded": [os.environ.get("LAYA_MODELS", "")], "device": "cpu"})

    def do_POST(self):
        if KEY and self.headers.get("Authorization") != "Bearer " + KEY:
            return self.send(401, {"detail": "invalid or missing bearer token"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        spam = 0.96 if "casino" in body["state"]["comment"] else 0.03
        self.send(200, {"answers": {"spam": {"type": "choice", "probabilities": {"A": 1 - spam, "B": spam}}},
                        "usage": {}, "routing": {"model": "english"}})


def main():
    http.server.HTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
'''


def build_fake_wheel(directory: str) -> str:
    """A valid wheel for a package named 'laya' containing the stand-in server."""
    name = os.path.join(directory, "laya-0.0.1-py3-none-any.whl")
    files = {
        "laya/__init__.py": '__version__ = "0.0.1-fake"\n',
        "laya/serve.py": FAKE_SERVE,
        "laya-0.0.1.dist-info/METADATA": "Metadata-Version: 2.1\nName: laya\nVersion: 0.0.1\n",
        "laya-0.0.1.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    with zipfile.ZipFile(name, "w") as zf:
        for path, content in files.items():
            data = content.encode()
            zf.writestr(path, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.append(f"{path},sha256={digest},{len(data)}")
        record.append("laya-0.0.1.dist-info/RECORD,,")
        zf.writestr("laya-0.0.1.dist-info/RECORD", "\n".join(record) + "\n")
    return name


def wait_for(predicate, timeout=20.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class ManagedLayaTests(unittest.TestCase):
    def setUp(self):
        self._delays = laya_manager.RESTART_DELAYS
        laya_manager.RESTART_DELAYS = (0.2, 0.2, 0.2)
        os.environ["AFTERWORD_SECRET_SHOULD_NOT_LEAK"] = "leak"
        self.app = make_app()
        self.manager = self.app.laya_manager
        self.app.settings.update({"laya_enabled": True, "laya_source": "managed",
                                  "moderation_mode": "automatic", "automatic_decider": "laya"})

    def tearDown(self):
        laya_manager.RESTART_DELAYS = self._delays
        os.environ.pop("AFTERWORD_SECRET_SHOULD_NOT_LEAK", None)
        cleanup(self.app)

    def fake_install(self):
        os.makedirs(self.manager.root, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", self.manager.venv], check=True)
        purelib = subprocess.run([self.manager.python, "-c",
                                  "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        os.makedirs(os.path.join(purelib, "laya"))
        with open(os.path.join(purelib, "laya", "__init__.py"), "w") as fh:
            fh.write('__version__ = "0.0.1-fake"\n')
        with open(os.path.join(purelib, "laya", "serve.py"), "w") as fh:
            fh.write(FAKE_SERVE)
        with open(self.manager.marker, "w") as fh:
            json.dump({"version": "0.0.1-fake", "device": "cpu"}, fh)

    def test_not_installed(self):
        self.assertEqual(self.manager.state, "absent")
        self.assertIsNone(self.manager.base_url)
        client = Client(self.app)
        self.assertEqual(post_comment(client).status, 202)       # held, not lost
        row = self.app.db.conn().execute("SELECT status, reason FROM comments").fetchone()
        self.assertEqual(row[0], "pending")
        self.assertIn("not installed", row[1])

    def test_lifecycle(self):
        self.fake_install()
        self.assertTrue(self.manager.installed())
        self.assertIsNone(self.manager.start())
        self.assertTrue(wait_for(lambda: self.manager.state == "running"), self.manager.status())

        # The service is loopback-only, keyed, and did not inherit our environment.
        with open(os.path.join(self.manager.root, "seen-env.json")) as fh:
            env = json.load(fh)
        self.assertEqual(env["LAYA_HOST"], "127.0.0.1")
        self.assertEqual(env["LAYA_API_KEY"], self.manager.api_key)
        self.assertEqual(env["LAYA_DEVICE"], "cpu")
        self.assertEqual(env["HF_HOME"], os.path.join(self.manager.root, "hf"))
        self.assertNotIn("AFTERWORD_SECRET_SHOULD_NOT_LEAK", env)

        # Comments flow through the managed service.
        client = Client(self.app)
        self.assertEqual(post_comment(client, body="Great write-up").status, 201)
        post_comment(client, body="casino casino")
        statuses = [r[0] for r in self.app.db.conn().execute("SELECT status FROM comments ORDER BY id")]
        self.assertEqual(statuses, ["approved", "rejected"])

        # A crash is noticed and the service comes back on a new process.
        old_pid = self.manager.status()["pid"]
        os.kill(old_pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: self.manager.state != "running", timeout=10))
        self.assertTrue(wait_for(lambda: self.manager.state == "running"
                                 and self.manager.status()["pid"] not in (None, old_pid)),
                        self.manager.status())
        self.app.laya.reset()
        self.assertTrue(self.app.laya.score(self.app.settings.all(), "a", "hello").ok)

        # Stop, then remove everything.
        pid = self.manager.status()["pid"]
        self.manager.stop()
        self.assertEqual(self.manager.state, "installed")
        self.assertTrue(wait_for(lambda: not os.path.exists(f"/proc/{pid}") or
                                 open(f"/proc/{pid}/stat").read().split()[2] == "Z", timeout=5))
        self.manager.uninstall()
        self.assertFalse(os.path.exists(self.manager.root))
        self.assertEqual(self.manager.state, "absent")

    def test_repeated_crashes_end_in_failed_state(self):
        self.fake_install()
        purelib = subprocess.run([self.manager.python, "-c",
                                  "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        with open(os.path.join(purelib, "laya", "serve.py"), "w") as fh:
            fh.write("import sys\nsys.exit(3)\n")
        self.manager.start()
        self.assertTrue(wait_for(lambda: self.manager.state == "failed", timeout=15), self.manager.status())
        self.assertIn("keeps stopping", self.manager.message)

    def test_install_refused_when_disabled(self):
        self.app.cfg.allow_laya_install = False
        error = self.manager.install()
        self.assertIn("turned off", error)
        self.assertEqual(self.manager.state, "absent")

    @unittest.skipUnless(os.environ.get("AFTERWORD_TEST_NETWORK") == "1", "set AFTERWORD_TEST_NETWORK=1")
    def test_install_from_dashboard(self):
        wheel = build_fake_wheel(tempfile.mkdtemp())
        self.app.cfg.laya_package = wheel
        self.assertIsNone(self.manager.install(device="auto"))    # "auto" skips the torch step
        self.assertTrue(wait_for(lambda: self.manager.state == "running", timeout=300),
                        (self.manager.status(), self.manager.install_log_tail()))
        self.assertEqual(self.manager.install_info()["version"], "0.0.1-fake")
        log = self.manager.install_log_tail(200)
        self.assertIn("Installing Laya", log)
        self.assertNotIn("PyTorch", log)


if __name__ == "__main__":
    unittest.main()
