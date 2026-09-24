"""Settings the owner edits from the dashboard.

Every setting is declared once in SPEC with its type, default and limits. The
dashboard forms, validation and defaults all come from this table, so there is
no second place where a limit could silently disagree.
"""
from __future__ import annotations

import dataclasses
import json
import threading
from typing import Any
from urllib.parse import urlsplit

DEFAULT_LAYA_QUESTION = (
    "Is `comment` spam rather than a genuine reader comment on a blog post?"
)
DEFAULT_LAYA_GENUINE = (
    "genuine: a real reader responding to the post, for example agreeing, "
    "disagreeing, asking a question or sharing something relevant"
)
DEFAULT_LAYA_SPAM = (
    "spam: advertising, SEO or link dropping, scams, phishing, gibberish, "
    "or generic text that could be pasted under any post"
)


@dataclasses.dataclass(frozen=True)
class Opt:
    key: str
    default: Any
    kind: str                     # bool, int, float, choice, str, text, origins, url, secret
    choices: tuple = ()
    min: float | None = None
    max: float | None = None
    maxlen: int = 2000


SPEC: list[Opt] = [
    # Blog
    Opt("site_origins", [], "origins"),
    Opt("comments_open", True, "bool"),
    # Moderation
    Opt("moderation_mode", "manual", "choice", choices=("manual", "assisted", "automatic")),
    Opt("automatic_decider", "basic", "choice", choices=("basic", "laya")),
    # What readers can write
    Opt("formatting", "basic", "choice", choices=("plain", "basic")),
    Opt("linkify", True, "bool"),
    Opt("ask_email", True, "bool"),
    Opt("thread_order", "oldest", "choice", choices=("oldest", "newest")),
    Opt("max_body_chars", 5000, "int", min=200, max=20000),
    # Basic checks
    Opt("min_seconds", 3, "int", min=0, max=120),
    Opt("max_links", 2, "int", min=0, max=50),
    Opt("hold_duplicates", True, "bool"),
    Opt("blocked_terms", "", "text", maxlen=20000),
    Opt("rate_per_ip", 5, "int", min=1, max=1000),          # per 10 minutes
    Opt("rate_global", 60, "int", min=1, max=100000),       # per hour
    Opt("purge_rejected_days", 30, "int", min=0, max=3650),
    # Laya
    Opt("laya_enabled", False, "bool"),
    Opt("laya_source", "managed", "choice", choices=("managed", "external")),
    Opt("laya_url", "", "url"),
    Opt("laya_api_key", "", "secret", maxlen=500),
    Opt("laya_model", "auto", "choice", choices=("auto", "english", "multilingual")),
    Opt("laya_timeout", 3.0, "float", min=0.5, max=30.0),
    Opt("laya_flag_at", 0.5, "float", min=0.0, max=1.0),
    Opt("laya_approve_below", 0.2, "float", min=0.0, max=1.0),
    Opt("laya_reject_at", 0.9, "float", min=0.0, max=1.0),
    Opt("laya_fallback", "hold", "choice", choices=("hold", "publish")),
    Opt("laya_question", DEFAULT_LAYA_QUESTION, "str", maxlen=500),
    Opt("laya_genuine", DEFAULT_LAYA_GENUINE, "str", maxlen=500),
    Opt("laya_spam", DEFAULT_LAYA_SPAM, "str", maxlen=500),
]
BY_KEY = {opt.key: opt for opt in SPEC}


def normalize_origin(value: str) -> str | None:
    value = value.strip().rstrip("/")
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = {"http": 80, "https": 443}[parts.scheme]
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default_port else "")


def coerce(opt: Opt, raw: Any) -> Any:
    """Turn a form value (or JSON value) into a valid setting, or raise ValueError."""
    if opt.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "on", "true", "yes")
    if opt.kind in ("int", "float"):
        try:
            value = int(str(raw).strip()) if opt.kind == "int" else float(str(raw).strip())
        except ValueError:
            raise ValueError("enter a number")
        if value != value:  # NaN
            raise ValueError("enter a number")
        if opt.min is not None and value < opt.min:
            raise ValueError(f"must be at least {opt.min:g}")
        if opt.max is not None and value > opt.max:
            raise ValueError(f"must be at most {opt.max:g}")
        return value
    if opt.kind == "choice":
        value = str(raw).strip()
        if value not in opt.choices:
            raise ValueError("pick one of the listed options")
        return value
    if opt.kind == "origins":
        items = raw if isinstance(raw, list) else str(raw).replace(",", "\n").splitlines()
        result = []
        for item in items:
            if not str(item).strip():
                continue
            origin = normalize_origin(str(item))
            if origin is None:
                raise ValueError(
                    f"“{str(item).strip()[:80]}” is not a site address like https://blog.example.com"
                )
            if origin not in result:
                result.append(origin)
        if len(result) > 20:
            raise ValueError("at most 20 addresses")
        return result
    if opt.kind == "url":
        value = str(raw).strip()
        if not value:
            return ""
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("enter an address like http://127.0.0.1:8000")
        return value.rstrip("/")
    # str, text, secret
    value = str(raw)
    if opt.kind == "str":
        value = " ".join(value.split())
    else:
        value = value.replace("\r\n", "\n")
    if len(value) > opt.maxlen:
        raise ValueError(f"at most {opt.maxlen} characters")
    return value


def cross_check(values: dict) -> dict[str, str]:
    """Rules that involve more than one setting."""
    errors = {}
    if values["laya_approve_below"] > values["laya_reject_at"]:
        errors["laya_approve_below"] = "must not be higher than the reject threshold"
    for key in ("laya_question", "laya_genuine", "laya_spam"):
        if not values[key].strip():
            errors[key] = "cannot be empty"
    return errors


class Settings:
    """Cached view of the settings table. Single process, so a lock is enough."""

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._cache: dict | None = None

    def all(self) -> dict:
        with self._lock:
            if self._cache is None:
                values = {opt.key: opt.default for opt in SPEC}
                for key, raw in self.db.conn().execute("SELECT key, value FROM settings"):
                    opt = BY_KEY.get(key)
                    if opt is None:
                        continue
                    try:
                        values[key] = coerce(opt, json.loads(raw))
                    except (ValueError, TypeError):
                        pass  # keep the default rather than run with a broken value
                self._cache = values
            return dict(self._cache)

    def get(self, key: str) -> Any:
        return self.all()[key]

    def update(self, changes: dict) -> dict[str, str]:
        """Validate and store. Returns {key: error}; stores nothing if any error."""
        errors: dict[str, str] = {}
        clean: dict[str, Any] = {}
        for key, raw in changes.items():
            opt = BY_KEY.get(key)
            if opt is None:
                errors[key] = "unknown setting"
                continue
            try:
                clean[key] = coerce(opt, raw)
            except ValueError as exc:
                errors[key] = str(exc)
        if errors:
            return errors
        merged = self.all()
        merged.update(clean)
        errors = cross_check(merged)
        if errors:
            return errors
        conn = self.db.conn()
        with conn:
            for key, value in clean.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
        with self._lock:
            self._cache = None
        return {}
