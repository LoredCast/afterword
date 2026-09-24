"""Installing and running Laya on this server, driven from the dashboard.

Everything lives under ``<data>/laya`` (a private virtualenv, the Hugging Face
model cache and two log files), so uninstalling is deleting one directory and
the comment server itself never imports torch.

The service is Laya's own ``laya-serve`` (``python -m laya.serve``), bound to
127.0.0.1 on a free port with a random API key. It is supervised: restarted if
it crashes, with backoff, and reported as failed after repeated crashes.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from . import security

MIN_FREE_BYTES = 4 * 1024 ** 3
READY_TIMEOUT = 30 * 60           # first start downloads the model
RESTART_DELAYS = (5, 30, 120)
_PASS_ENV = (
    "PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "HF_ENDPOINT", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
)
MODELS_FOR_SETTING = {"auto": "english", "english": "english", "multilingual": "multilingual"}


class LayaManager:
    def __init__(self, cfg, db, settings):
        self.cfg = cfg
        self.db = db
        self.settings = settings
        self.root = os.path.abspath(cfg.laya_dir)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._generation = 0
        self._failures = 0
        self.port: int | None = None
        self.state = "installed" if self.installed() else "absent"
        self.message = ""

    # -- paths ------------------------------------------------------------
    @property
    def venv(self) -> str:
        return os.path.join(self.root, "venv")

    @property
    def python(self) -> str:
        sub = "Scripts" if os.name == "nt" else "bin"
        exe = "python.exe" if os.name == "nt" else "python"
        return os.path.join(self.venv, sub, exe)

    @property
    def install_log(self) -> str:
        return os.path.join(self.root, "install.log")

    @property
    def serve_log(self) -> str:
        return os.path.join(self.root, "service.log")

    @property
    def marker(self) -> str:
        return os.path.join(self.root, "installed.json")

    @property
    def pidfile(self) -> str:
        return os.path.join(self.root, "service.pid")

    def installed(self) -> bool:
        return os.path.exists(self.marker) and os.path.exists(self.python)

    def install_info(self) -> dict:
        try:
            with open(self.marker, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    @property
    def api_key(self) -> str:
        key = self.db.meta_get("laya_managed_key")
        if not key:
            key = security.token(24)
            self.db.meta_set("laya_managed_key", key)
        return key

    @property
    def base_url(self) -> str | None:
        with self._lock:
            if self.state == "running" and self.port:
                return f"http://127.0.0.1:{self.port}"
            return None

    def not_running_reason(self) -> str:
        return {
            "absent": "Laya is not installed",
            "installing": "Laya is still being installed",
            "installed": "the Laya service is stopped",
            "starting": "the Laya service is still starting",
            "failed": "the Laya service failed to start",
        }.get(self.state, "the Laya service is not running")

    # -- state --------------------------------------------------------------
    def _set(self, state: str, message: str = "") -> None:
        with self._lock:
            self.state = state
            self.message = message

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
            return {
                "state": self.state,
                "message": self.message,
                "installed": self.installed(),
                "info": self.install_info(),
                "port": self.port,
                "pid": proc.pid if proc and proc.poll() is None else None,
                "can_install": self.cfg.allow_laya_install,
                "package": self.cfg.laya_package,
            }

    @staticmethod
    def _tail(path: str, lines: int) -> str:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 64 * 1024))
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            return ""
        return "\n".join(data.splitlines()[-lines:])

    def install_log_tail(self, lines: int = 40) -> str:
        return self._tail(self.install_log, lines)

    def serve_log_tail(self, lines: int = 20) -> str:
        return self._tail(self.serve_log, lines)

    # -- environment -----------------------------------------------------------
    def _base_env(self) -> dict:
        env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
        bin_dir = os.path.dirname(self.python)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "/usr/bin:/bin")
        env["HOME"] = self.root
        env["HF_HOME"] = os.path.join(self.root, "hf")
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PIP_NO_INPUT"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    # -- install ---------------------------------------------------------------
    def preflight(self) -> list[str]:
        """Problems that would stop an install, in plain language."""
        problems = []
        if not self.cfg.allow_laya_install:
            problems.append("Installing from the dashboard is turned off on this server "
                            "(AFTERWORD_LAYA_INSTALL=0). Use an existing Laya server instead.")
        if sys.version_info < (3, 10):
            problems.append("Laya needs Python 3.10 or newer.")
        try:
            import ensurepip  # noqa: F401
            import venv  # noqa: F401
        except ImportError:
            problems.append("Python's venv support is missing. On Debian or Ubuntu install "
                            "the python3-venv package, then try again.")
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free < MIN_FREE_BYTES:
            problems.append(f"Laya needs about 4 GB of free disk space; "
                            f"{free / 1024 ** 3:.1f} GB is free.")
        return problems

    @staticmethod
    def memory_gb() -> float | None:
        try:
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024 ** 2
        except (OSError, ValueError):
            pass
        return None

    def install(self, device: str = "cpu") -> str | None:
        """Start installing in the background. Returns an error message or None."""
        with self._lock:
            if self.state in ("installing", "starting", "running"):
                return "Laya is already installed or being installed."
            problems = self.preflight()
            if problems:
                return " ".join(problems)
            self._set("installing", "Preparing…")
        threading.Thread(target=self._install_job, args=(device,), daemon=True,
                         name="laya-install").start()
        return None

    def _install_job(self, device: str) -> None:
        env = self._base_env()
        try:
            with open(self.install_log, "w", encoding="utf-8") as log:
                def step(message: str, cmd: list[str], timeout: int, required: bool = True) -> None:
                    self._set("installing", message)
                    log.write(f"\n### {message}\n$ {' '.join(cmd)}\n")
                    log.flush()
                    done = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                                          cwd=self.root, timeout=timeout)
                    if done.returncode != 0:
                        if not required:
                            log.write("(not needed to continue; carrying on)\n")
                            return
                        raise RuntimeError(f"{message.rstrip('…')} failed (exit code "
                                           f"{done.returncode}). The install log below has details.")

                memory = self.memory_gb()
                log.write(f"Afterword Laya install, package {self.cfg.laya_package}, device {device}\n")
                if memory is not None and memory < 2.5:
                    log.write(f"WARNING: this machine has {memory:.1f} GB of memory; "
                              "Laya's English model needs about 2 GB while running.\n")
                if os.path.exists(self.venv):
                    shutil.rmtree(self.venv)
                step("Creating a private Python environment…",
                     [sys.executable, "-m", "venv", self.venv], 600)
                pip = [self.python, "-m", "pip", "install", "--no-cache-dir"]
                step("Updating pip…", pip + ["--upgrade", "pip"], 900, required=False)
                if device == "cpu" and sys.platform.startswith("linux") and \
                        platform.machine().lower() in ("x86_64", "amd64"):
                    # PyPI's default Linux build bundles CUDA (several GB). The CPU
                    # build is a fraction of the size and is all a blog server needs.
                    step("Installing PyTorch (CPU build)…",
                         pip + ["--index-url", "https://download.pytorch.org/whl/cpu", "torch"], 3600)
                step("Installing Laya…", pip + [self.cfg.laya_package], 3600)
                step("Checking the installation…",
                     [self.python, "-I", "-c", "import laya, laya.serve; print('laya', laya.__version__)"],
                     300)
                version = subprocess.run(
                    [self.python, "-I", "-c", "import laya; print(laya.__version__)"],
                    capture_output=True, text=True, env=env, timeout=300,
                ).stdout.strip()
                with open(self.marker, "w", encoding="utf-8") as fh:
                    json.dump({"version": version, "device": device, "package": self.cfg.laya_package,
                               "installed_at": int(time.time())}, fh)
                log.write("\nInstalled. Starting the service; the first start downloads the model.\n")
            self._set("installed", "")
            self.start()
        except Exception as exc:  # noqa: BLE001 - report every failure on the dashboard
            self._set("failed", str(exc) or exc.__class__.__name__)

    def uninstall(self) -> None:
        self.stop()
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self._set("absent", "")

    # -- run ---------------------------------------------------------------------
    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _kill_stale(self) -> None:
        """Stop a service left behind by a previous run that was killed hard."""
        try:
            with open(self.pidfile, encoding="ascii") as fh:
                pid = int(fh.read().strip())
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return
        if self.venv in cmdline and "laya.serve" in cmdline:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def start(self) -> str | None:
        with self._lock:
            if not self.installed():
                return "Laya is not installed."
            if self._proc is not None and self._proc.poll() is None:
                return None
            self._kill_stale()
            self._generation += 1
            generation = self._generation
            self.port = self._free_port()
            cpus = os.cpu_count() or 2
            env = self._base_env()
            env.update({
                "LAYA_HOST": "127.0.0.1",   # laya-serve's own default is 0.0.0.0
                "LAYA_PORT": str(self.port),
                "LAYA_API_KEY": self.api_key,
                "LAYA_PRELOAD": "1",
                "LAYA_MODELS": MODELS_FOR_SETTING.get(self.settings.get("laya_model"), "english"),
                "LAYA_THREADS": str(max(1, min(4, cpus // 2))),
                "LAYA_LOG_LEVEL": "warning",
            })
            if self.install_info().get("device", "cpu") == "cpu":
                env["LAYA_DEVICE"] = "cpu"
            log = open(self.serve_log, "ab")
            log.write(f"\n--- starting laya.serve on 127.0.0.1:{self.port} at "
                      f"{time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
            log.flush()
            self._proc = subprocess.Popen(
                [self.python, "-I", "-m", "laya.serve"], env=env, cwd=self.root,
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            )
            log.close()
            with open(self.pidfile, "w", encoding="ascii") as fh:
                fh.write(str(self._proc.pid))
            self._set("starting", "Loading the model. The first start downloads it from "
                                  "Hugging Face (about 1–2 GB) and can take several minutes.")
            threading.Thread(target=self._watch, args=(self._proc, self.port, generation),
                             daemon=True, name="laya-watch").start()
        return None

    def _healthy(self, port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                data = json.loads(resp.read(64 * 1024))
            return isinstance(data, dict) and data.get("status") == "ok"
        except (OSError, ValueError):
            return False

    def _watch(self, proc: subprocess.Popen, port: int, generation: int) -> None:
        deadline = time.time() + READY_TIMEOUT
        while proc.poll() is None:
            if generation != self._generation:
                return
            if self.state == "starting":
                if self._healthy(port):
                    self._failures = 0
                    self._set("running", "")
                elif time.time() > deadline:
                    proc.terminate()
                    self._set("failed", "The service did not become ready within 30 minutes.")
                    return
            time.sleep(2 if self.state == "starting" else 5)
        if generation != self._generation:
            return  # stopped on purpose
        self._failures += 1
        code = proc.returncode
        if self._failures <= len(RESTART_DELAYS):
            delay = RESTART_DELAYS[self._failures - 1]
            self._set("starting", f"The service stopped unexpectedly (exit code {code}); "
                                  f"restarting in {delay} seconds.")
            time.sleep(delay)
            if generation == self._generation:
                self.start()
        else:
            self._set("failed", f"The service keeps stopping (last exit code {code}). "
                                "The service log below usually says why, for example "
                                "running out of memory or a failed model download.")

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            proc, self._proc = self._proc, None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
            try:
                os.remove(self.pidfile)
            except OSError:
                pass
            self.port = None
            if self.state not in ("absent", "installing"):
                self._set("installed" if self.installed() else "absent", "")

    def restart(self) -> str | None:
        self.stop()
        self._failures = 0
        return self.start()
