"""Shared test helpers. Standard library only."""
from __future__ import annotations

import http.server
import io
import json
import re
import shutil
import tempfile
import threading
import time
import urllib.parse

from afterword.app import App
from afterword.config import Config

BLOG = "https://blog.example.com"


def make_app(**cfg) -> App:
    directory = tempfile.mkdtemp(prefix="afterword-test-")
    options = {"data_dir": directory, "dev": False, "public_url": "https://comments.example.com"}
    options.update(cfg)
    app = App(Config(**options))
    app._test_dir = directory
    app.settings.update({"site_origins": BLOG, "min_seconds": 0, "rate_per_ip": 1000,
                         "rate_global": 10000})
    return app


def cleanup(app: App) -> None:
    app.stop()
    shutil.rmtree(app._test_dir, ignore_errors=True)


class Result:
    def __init__(self, status: str, headers: list, body: bytes):
        self.status_line = status
        self.status = int(status.split()[0])
        self.header_list = headers
        self.headers = {k.lower(): v for k, v in headers}
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self):
        return json.loads(self.body)

    def cookies(self) -> list[str]:
        return [v for k, v in self.header_list if k.lower() == "set-cookie"]


class Client:
    def __init__(self, app: App, remote_addr: str = "203.0.113.7", scheme: str = "https",
                 host: str = "comments.example.com"):
        self.app = app
        self.remote_addr = remote_addr
        self.scheme = scheme
        self.host = host
        self.jar: dict[str, str] = {}

    def request(self, method: str, path: str, *, query: dict | None = None, form: dict | None = None,
                json_body=None, raw: bytes | None = None, headers: dict | None = None,
                content_type: str | None = None, remote_addr: str | None = None) -> Result:
        body = b""
        if form is not None:
            body = urllib.parse.urlencode(form, doseq=True).encode()
            content_type = content_type or "application/x-www-form-urlencoded"
        elif json_body is not None:
            body = json.dumps(json_body).encode()
            content_type = content_type or "application/json"
        elif raw is not None:
            body = raw
        env = {
            "REQUEST_METHOD": method, "PATH_INFO": path, "SCRIPT_NAME": "",
            "QUERY_STRING": urllib.parse.urlencode(query or {}), "wsgi.url_scheme": self.scheme,
            "HTTP_HOST": self.host, "REMOTE_ADDR": remote_addr or self.remote_addr,
            "wsgi.input": io.BytesIO(body), "CONTENT_LENGTH": str(len(body)),
        }
        if content_type:
            env["CONTENT_TYPE"] = content_type
        if self.jar:
            env["HTTP_COOKIE"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        for key, value in (headers or {}).items():
            name = key.upper().replace("-", "_")
            env[name if name in ("CONTENT_TYPE", "CONTENT_LENGTH") else "HTTP_" + name] = value
        captured = {}

        def start_response(status, response_headers):
            captured["status"], captured["headers"] = status, response_headers

        data = b"".join(self.app(env, start_response))
        result = Result(captured["status"], captured["headers"], data)
        for cookie in result.cookies():
            pair = cookie.split(";")[0]
            name, _, value = pair.partition("=")
            if "Max-Age=0" in cookie:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value
        return result

    # conveniences ---------------------------------------------------------
    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def admin_post(self, path, form: dict, origin: str = "https://comments.example.com", **kw):
        form = dict(form)
        form.setdefault("csrf", self.csrf())  # one token per session, valid on every form
        headers = {"Origin": origin} if origin else {}
        headers.update(kw.pop("headers", {}))
        return self.post(path, form=form, headers=headers, **kw)

    def csrf(self, page: str = "/admin/comments") -> str:
        text = self.get(page).text
        match = re.search(r'name="csrf" value="([^"]+)"', text)
        assert match, f"no CSRF token on {page}"
        return match.group(1)


def set_password_and_login(app: App, client: Client, password: str = "correct horse battery") -> None:
    from afterword import security
    app.db.meta_set("admin_password", security.hash_password(password))
    result = client.post("/admin/login", form={"password": password},
                         headers={"Origin": "https://comments.example.com"})
    assert result.status == 303, result.text


def thread_token(client: Client, thread: str = "/posts/hello") -> str:
    result = client.get("/api/v1/thread", query={"id": thread}, headers={"Origin": BLOG})
    return result.json()["form"]["token"]


def post_comment(client: Client, *, thread: str = "/posts/hello", author: str = "Ada",
                 body: str = "Lovely post, thank you.", email: str = "", token: str | None = None,
                 origin: str = BLOG, extra: dict | None = None, **kw) -> Result:
    payload = {"thread": thread, "author": author, "body": body, "email": email,
               "page": BLOG + thread, "website": ""}
    payload["token"] = token if token is not None else thread_token(client, thread)
    payload.update(extra or {})
    return client.post("/api/v1/comments", json_body=payload,
                       headers={"Origin": origin} if origin else {}, **kw)


class FakeLaya:
    """A tiny laya-serve stand-in. Spam if the comment contains a trigger word."""

    def __init__(self, api_key: str | None = "test-key"):
        self.api_key = api_key
        self.delay = 0.0
        self.mode = "ok"            # ok | error500 | garbage | missing
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {"status": "ok", "loaded": ["english"], "device": "cpu"})
                else:
                    self._send(404, {"detail": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                outer.requests.append({"headers": dict(self.headers), "body": body})
                if outer.api_key and self.headers.get("Authorization") != "Bearer " + outer.api_key:
                    return self._send(401, {"detail": "invalid or missing bearer token"})
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.mode == "error500":
                    return self._send(500, {"detail": "boom"})
                if outer.mode == "garbage":
                    return self._send(200, b"<html>not json</html>")
                if outer.mode == "missing":
                    return self._send(200, {"answers": {}})
                comment = body["state"]["comment"].lower()
                spam = 0.97 if "casino" in comment else 0.55 if "maybe" in comment else 0.04
                self._send(200, {
                    "model": "convaiinnovations/laya",
                    "answers": {"spam": {"type": "choice", "choice": "B" if spam > 0.5 else "A",
                                         "probabilities": {"A": round(1 - spam, 4), "B": spam},
                                         "confidence": max(spam, 1 - spam)}},
                    "usage": {"input_tokens": 50, "output_tokens": 0},
                    "routing": {"model": "english", "reason": "test"},
                })

        class QuietServer(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass  # e.g. broken pipe after a client timed out on purpose

        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def use_fake_laya(app: App, fake: FakeLaya, **settings) -> None:
    changes = {"laya_enabled": True, "laya_source": "external", "laya_url": fake.url,
               "laya_api_key": fake.api_key or "", "laya_timeout": 2.0}
    changes.update(settings)
    errors = app.settings.update(changes)
    assert not errors, errors
    app.laya.reset()
