"""The WSGI application: wiring, static files, security headers, housekeeping."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import traceback

from . import admin, api, pseudonyms, security
from .db import Database
from .laya import LayaClient
from .laya_manager import LayaManager
from .settings import Settings
from .web import HTTPError, Markup, Request, Response, json_response, redirect, render

log = logging.getLogger("afterword")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STATIC_FILES = {
    "/widget.js": ("widget.js", "text/javascript; charset=utf-8", "public, max-age=3600"),
    "/afterword.css": ("afterword.css", "text/css; charset=utf-8", "public, max-age=3600"),
    "/admin/static/admin.css": ("admin.css", "text/css; charset=utf-8", "public, max-age=86400"),
    "/admin/static/admin.js": ("admin.js", "text/javascript; charset=utf-8", "public, max-age=86400"),
}
ADMIN_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
IP_KEY_RETENTION_DAYS = 30
SETUP_CODE_HOURS = 24


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg.data_dir, mode=0o700, exist_ok=True)
        self.db = Database(cfg.db_path)
        self.secret = self._load_secret()
        self.settings = Settings(self.db)
        self.form_tokens = security.FormTokens(self.secret)
        self.limiter = security.RateLimiter()
        self.flashes: dict[str, tuple[str, str]] = {}
        self.laya_manager = LayaManager(cfg, self.db, self.settings)
        self.laya = LayaClient(self._laya_endpoint)
        self.static = self._load_static()
        self.static_version = hashlib.sha256(
            self.static["/admin/static/admin.css"][0] + self.static["/admin/static/admin.js"][0]
        ).hexdigest()[:10]
        self._stop = threading.Event()

    # -- setup -------------------------------------------------------------
    def _load_secret(self) -> bytes:
        value = self.db.meta_get("secret")
        if not value:
            value = os.urandom(32).hex()
            self.db.meta_set("secret", value)
        return bytes.fromhex(value)

    def _load_static(self) -> dict:
        files = {}
        for path, (name, ctype, cache) in STATIC_FILES.items():
            with open(os.path.join(STATIC_DIR, name), "rb") as fh:
                data = fh.read()
            etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
            files[path] = (data, ctype, cache, etag)
        return files

    def new_setup_code(self) -> str | None:
        """If no password is set yet, create a one-time setup code and return it."""
        if self.db.meta_get("admin_password"):
            return None
        code = security.setup_code()
        self.db.meta_set("setup_code_hash", security.sha256_hex(code))
        self.db.meta_set("setup_code_expires", str(int(time.time()) + SETUP_CODE_HOURS * 3600))
        return code

    def _laya_endpoint(self, s: dict):
        if s["laya_source"] == "external":
            if not s["laya_url"]:
                return None, None, "no Laya server address is set"
            return s["laya_url"], s["laya_api_key"] or None, None
        url = self.laya_manager.base_url
        if url is None:
            return None, None, self.laya_manager.not_running_reason()
        return url, self.laya_manager.api_key, None

    # -- background --------------------------------------------------------
    def start_background(self) -> None:
        s = self.settings.all()
        if s["laya_enabled"] and s["laya_source"] == "managed" and self.laya_manager.installed():
            self.laya_manager.start()
        threading.Thread(target=self._maintenance_loop, daemon=True, name="maintenance").start()

    def stop(self) -> None:
        self._stop.set()
        self.laya_manager.stop()

    def _maintenance_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.maintenance()
            except Exception:  # noqa: BLE001
                log.exception("maintenance failed")
            self._stop.wait(600)

    def maintenance(self, now: float | None = None) -> None:
        now = int(now or time.time())
        s = self.settings.all()
        conn = self.db.conn()
        with conn:
            if s["purge_rejected_days"] > 0:
                conn.execute("DELETE FROM comments WHERE status = 'rejected' AND "
                             "COALESCE(decided_at, created_at) < ?",
                             (now - s["purge_rejected_days"] * 86400,))
            conn.execute("UPDATE comments SET ip_key = NULL WHERE ip_key IS NOT NULL AND created_at < ?",
                         (now - IP_KEY_RETENTION_DAYS * 86400,))
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            pseudonyms.forget_orphans(conn)
            conn.execute("DELETE FROM counters WHERE day < ?",
                         (time.strftime("%Y-%m-%d", time.gmtime(now - 90 * 86400)),))
        self.form_tokens.prune(now)
        self.limiter.prune(now)
        if len(self.flashes) > 1000:
            self.flashes.clear()

    # -- requests ----------------------------------------------------------
    def dispatch(self, req: Request) -> Response:
        if req.path == "/healthz":
            return json_response({"status": "ok"})
        if req.path in self.static and req.method in ("GET", "HEAD"):
            return self._static(req)
        resp = api.handle(self, req)
        if resp is not None:
            return resp
        resp = admin.handle(self, req)
        if resp is not None:
            return resp
        if req.path == "/":
            return redirect(req.url("/admin"))
        return Response("Not found", status=404, content_type="text/plain; charset=utf-8")

    def _static(self, req: Request) -> Response:
        data, ctype, cache, etag = self.static[req.path]
        headers = {"Cache-Control": cache, "ETag": etag}
        if req.path in ("/widget.js", "/afterword.css"):
            # Loaded by blogs on other origins; "*" lets them use Subresource Integrity.
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        if req.header("If-None-Match") == etag:
            return Response(b"", status=304, content_type=ctype, headers=headers)
        return Response(b"" if req.method == "HEAD" else data, content_type=ctype, headers=headers)

    def _secure(self, req: Request, resp: Response) -> None:
        resp.set_header("X-Content-Type-Options", "nosniff")
        if req.path.startswith("/admin") and not req.path.startswith("/admin/static/"):
            resp.set_header("Content-Security-Policy", ADMIN_CSP)
            resp.set_header("X-Frame-Options", "DENY")
            # Not "no-referrer": with that policy browsers send "Origin: null" on
            # form posts, which would defeat the same-origin check on every form.
            resp.set_header("Referrer-Policy", "same-origin")
            resp.set_header("Cross-Origin-Opener-Policy", "same-origin")
            resp.set_header("Cache-Control", "no-store")
        elif req.path.startswith("/api/"):
            resp.set_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            resp.set_header("Referrer-Policy", "no-referrer")

    def _error(self, req: Request, status: int, code: str, message: str) -> Response:
        if req.path.startswith("/api/"):
            return json_response({"error": code, "message": message}, status=status)
        if req.path.startswith("/admin"):
            body = render('<section class="narrow"><h1>Something went wrong</h1><p>{m}</p>'
                          '<p><a href="{h}">Back to the dashboard</a></p></section>',
                          m=message, h=req.url("/admin"))
            return admin.page(self, req, "Error", Markup(body), status=status)
        return Response(message, status=status, content_type="text/plain; charset=utf-8")

    def __call__(self, environ, start_response):
        req = Request(environ)
        try:
            resp = self.dispatch(req)
        except HTTPError as exc:
            resp = self._error(req, exc.status, exc.code, exc.message)
            for key, value in exc.headers.items():
                resp.set_header(key, value)
        except Exception:  # noqa: BLE001 - never show internals to the client
            log.error("unhandled error on %s %s\n%s", req.method, req.path, traceback.format_exc())
            resp = self._error(req, 500, "server_error", "The server had a problem. It has been logged.")
        self._secure(req, resp)
        return resp.wsgi(start_response)
