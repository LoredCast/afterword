"""Contract test against Laya's real HTTP server code.

Runs Laya's own ``laya.serve`` FastAPI app (routing, bearer auth, error
mapping) with only the model replaced by a stub. The stub validates every
question with Laya's own validator, extracted from the installed package's
source, so a question Laya would refuse fails this test.

Needs a Python with ``laya`` (torch not required), ``fastapi`` and ``uvicorn``:

    python -m venv /tmp/laya-contract
    /tmp/laya-contract/bin/pip install --no-deps laya==0.3.10
    /tmp/laya-contract/bin/pip install fastapi uvicorn
    AFTERWORD_LAYA_PYTHON=/tmp/laya-contract/bin/python python -m unittest discover -s tests
"""
import os
import socket
import subprocess
import textwrap
import time
import unittest
import urllib.request

from helpers import Client, cleanup, make_app, post_comment

LAYA_PYTHON = os.environ.get("AFTERWORD_LAYA_PYTHON")

RUNNER = textwrap.dedent(r'''
    import ast, inspect, os, sys
    import laya, laya.serve, uvicorn

    # Pull Laya's question validator out of its source (agent.py imports torch).
    pkg = os.path.dirname(laya.__file__)
    ns = {"Any": object}
    common = ast.parse(open(os.path.join(pkg, "common.py")).read())
    agent = ast.parse(open(os.path.join(pkg, "agent.py")).read())
    wanted = []
    for node in common.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "QTYPES" for t in node.targets):
            wanted.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_noul_labels":
            wanted.append(node)
    for node in agent.body:
        if isinstance(node, ast.ClassDef) and node.name == "Agent":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_check_question":
                    item.decorator_list = []
                    wanted.append(item)
    assert len(wanted) == 3, [getattr(n, "name", "QTYPES") for n in wanted]
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "laya-validator", "exec"), ns)
    check_question = ns["_check_question"]

    class StubRouter:
        loaded = ["english"]
        def predict(self, state, questions, model=None):
            assert isinstance(state, dict), state
            for qid, q in questions.items():
                check_question(qid, q)
            q = questions["spam"]
            spam = 0.93 if "casino" in state["comment"] else 0.07
            keys = list(q["criteria"])
            probs = {keys[0]: round(1 - spam, 4), keys[1]: spam}
            return {"model": "stub", "answers": {"spam": {"type": "choice", "choice": max(probs, key=probs.get),
                    "probabilities": probs, "confidence": max(spam, 1 - spam)}},
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                    "routing": {"model": model or "english", "reason": "stub"}}

    app = laya.serve.create_app(router=StubRouter())
    print("laya", laya.__version__, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
''')


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(LAYA_PYTHON, "set AFTERWORD_LAYA_PYTHON to a Python with laya, fastapi, uvicorn")
class LayaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, LAYA_API_KEY="contract-key")
        cls.proc = subprocess.Popen([LAYA_PYTHON, "-c", RUNNER, str(cls.port)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.url = f"http://127.0.0.1:{cls.port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.url + "/health", timeout=1)
                return
            except OSError:
                if cls.proc.poll() is not None:
                    raise RuntimeError("laya.serve exited:\n" + cls.proc.stdout.read())
                time.sleep(0.1)
        raise RuntimeError("laya.serve did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)

    def setUp(self):
        self.app = make_app()
        self.app.settings.update({"laya_enabled": True, "laya_source": "external", "laya_url": self.url,
                                  "laya_api_key": "contract-key", "moderation_mode": "automatic",
                                  "automatic_decider": "laya"})

    def tearDown(self):
        cleanup(self.app)

    def test_health(self):
        health = self.app.laya.health(self.app.settings.all())
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["loaded"], ["english"])

    def test_scores_through_real_server(self):
        s = self.app.settings.all()
        good = self.app.laya.score(s, "Ada", "Thanks, the section on kerning helped.")
        spam = self.app.laya.score(s, "Bob", "casino bonus")
        self.assertTrue(good.ok and spam.ok, (good, spam))
        self.assertAlmostEqual(good.score, 0.07, places=3)
        self.assertAlmostEqual(spam.score, 0.93, places=3)
        self.assertEqual(good.model, "english")

    def test_model_choice_is_honoured(self):
        self.app.settings.update({"laya_model": "multilingual"})
        result = self.app.laya.score(self.app.settings.all(), "Ada", "Hallo zusammen")
        self.assertEqual(result.model, "multilingual")

    def test_custom_question_wording_passes_laya_validation(self):
        self.app.settings.update({"laya_question": "Ist `comment` Werbung?", "laya_genuine": "echt",
                                  "laya_spam": "Werbung"})
        self.assertTrue(self.app.laya.score(self.app.settings.all(), "a", "b").ok)

    def test_wrong_key_is_reported(self):
        self.app.settings.update({"laya_api_key": "nope"})
        result = self.app.laya.score(self.app.settings.all(), "a", "b")
        self.assertFalse(result.ok)
        self.assertIn("API key", result.error)

    def test_end_to_end_comment(self):
        client = Client(self.app)
        self.assertEqual(post_comment(client, body="Clear and useful, thanks!").status, 201)
        post_comment(client, body="casino casino casino")
        rows = self.app.db.conn().execute("SELECT status, decided_by FROM comments ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("approved", "laya"), ("rejected", "laya")])


if __name__ == "__main__":
    unittest.main()
