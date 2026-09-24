"""A deliberately small WSGI layer.

HTML is built with ``render()``, which escapes every interpolated value unless it
is already ``Markup``. Forgetting to escape therefore produces visible
``&lt;`` noise instead of an injection.
"""
from __future__ import annotations

import html
import json
import urllib.parse
from http import HTTPStatus


class Markup(str):
    """A string that is already safe HTML."""
    __slots__ = ()


def escape(value) -> Markup:
    if isinstance(value, Markup):
        return value
    if value is None:
        return Markup("")
    return Markup(html.escape(str(value), quote=True))


def render(template: str, **values) -> Markup:
    return Markup(template.format_map({k: escape(v) for k, v in values.items()}))


def join(parts, sep: str = "") -> Markup:
    return Markup(escape(sep).join(escape(p) for p in parts))


class HTTPError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


class Request:
    def __init__(self, environ: dict):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "") or "/"
        self.script_name = environ.get("SCRIPT_NAME", "").rstrip("/")
        self.query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        self.remote_addr = environ.get("REMOTE_ADDR", "") or ""
        self.scheme = environ.get("wsgi.url_scheme", "http")
        self._body: bytes | None = None
        self._form: dict | None = None

    def header(self, name: str) -> str | None:
        key = name.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            return self.environ.get(key)
        return self.environ.get("HTTP_" + key)

    @property
    def host(self) -> str:
        return self.environ.get("HTTP_HOST") or self.environ.get("SERVER_NAME", "localhost")

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}".lower()

    def arg(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default

    def url(self, path: str) -> str:
        return self.script_name + path

    def cookies(self) -> dict[str, str]:
        jar = {}
        for part in (self.header("Cookie") or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name and name not in jar:
                jar[name] = value
        return jar

    def body(self, limit: int) -> bytes:
        if self._body is not None:
            return self._body
        raw_length = self.environ.get("CONTENT_LENGTH") or "0"
        try:
            length = int(raw_length)
        except ValueError:
            raise HTTPError(400, "bad_request", "Invalid Content-Length.")
        if length < 0:
            raise HTTPError(400, "bad_request", "Invalid Content-Length.")
        if length > limit:
            raise HTTPError(413, "too_large", "The request is too large.")
        stream = self.environ.get("wsgi.input")
        data = stream.read(length) if (stream is not None and length) else b""
        self._body = data
        return data

    def json(self, limit: int):
        ctype = (self.header("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HTTPError(415, "unsupported_media_type", "Send JSON with Content-Type: application/json.")
        try:
            return json.loads(self.body(limit).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPError(400, "invalid_json", "The request body is not valid JSON.")

    def form(self, limit: int = 256 * 1024) -> dict[str, list[str]]:
        if self._form is None:
            ctype = (self.header("Content-Type") or "").split(";")[0].strip().lower()
            if self.method != "POST" or ctype != "application/x-www-form-urlencoded":
                self._form = {}
            else:
                try:
                    text = self.body(limit).decode("utf-8")
                except UnicodeDecodeError:
                    raise HTTPError(400, "bad_request", "Form data must be UTF-8.")
                self._form = urllib.parse.parse_qs(text, keep_blank_values=True, max_num_fields=2000)
        return self._form

    def field(self, name: str, default: str = "") -> str:
        values = self.form().get(name)
        return values[0] if values else default

    def fields(self, name: str) -> list[str]:
        return self.form().get(name, [])


class Response:
    def __init__(self, body: bytes | str = b"", status: int = 200,
                 content_type: str = "text/html; charset=utf-8", headers: dict | None = None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers: list[tuple[str, str]] = [("Content-Type", content_type)]
        for key, value in (headers or {}).items():
            self.headers.append((key, value))

    def set_header(self, name: str, value: str) -> None:
        self.headers = [(k, v) for k, v in self.headers if k.lower() != name.lower()]
        self.headers.append((name, value))

    def has_header(self, name: str) -> bool:
        return any(k.lower() == name.lower() for k, _ in self.headers)

    def set_cookie(self, name: str, value: str, *, max_age: int | None, secure: bool,
                   path: str = "/", samesite: str = "Strict") -> None:
        parts = [f"{name}={value}", f"Path={path}", "HttpOnly", f"SameSite={samesite}"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if secure:
            parts.append("Secure")
        self.headers.append(("Set-Cookie", "; ".join(parts)))

    def wsgi(self, start_response):
        phrase = HTTPStatus(self.status).phrase
        headers = self.headers + [("Content-Length", str(len(self.body)))]
        start_response(f"{self.status} {phrase}", headers)
        return [self.body]


def json_response(data, status: int = 200, headers: dict | None = None) -> Response:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return Response(body, status=status, content_type="application/json; charset=utf-8",
                    headers=headers)


def redirect(location: str, status: int = 303) -> Response:
    return Response(b"", status=status, headers={"Location": location})
