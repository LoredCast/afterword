"""Public JSON API used by the widget.

GET  /api/v1/thread?id=<thread>   approved comments + a fresh form token
POST /api/v1/comments             submit a comment

Only approved comments are ever returned, and only these fields: id, author,
created, text and body. Email addresses, network keys, moderation reasons and
Laya scores never leave the dashboard.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import moderation, security
from . import text as textmod
from .settings import normalize_origin
from .web import HTTPError, Request, Response, json_response

MAX_NAME = 80
MAX_EMAIL = 254
MAX_THREAD = 300
MAX_REQUEST_BYTES = 64 * 1024
MAX_THREAD_COMMENTS = 1000
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_thread(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    thread = unicodedata.normalize("NFC", raw.strip())
    if not thread or len(thread) > MAX_THREAD:
        return None
    for ch in thread:
        if ch.isspace() or unicodedata.category(ch)[0] == "C":
            return None
    return thread


def public_comment(row, settings: dict) -> dict:
    return {
        "id": row["public_id"],
        "author": row["author"],
        "created": iso(row["created_at"]),
        "text": row["body"],
        "body": textmod.render(row["body"], formatting=settings["formatting"],
                               linkify=settings["linkify"]),
    }


def allowed_origin(req: Request, settings: dict) -> str | None:
    origin = req.header("Origin")
    if not origin:
        return None
    normalized = normalize_origin(origin)
    return normalized if normalized and normalized in settings["site_origins"] else None


def _cors(req: Request, settings: dict) -> dict:
    headers = {"Vary": "Origin", "Cache-Control": "no-store"}
    origin = allowed_origin(req, settings)
    if origin:
        headers["Access-Control-Allow-Origin"] = req.header("Origin")
    return headers


def _error(status: int, code: str, message: str, headers: dict, field: str | None = None,
           extra_headers: dict | None = None) -> Response:
    payload = {"error": code, "message": message}
    if field:
        payload["field"] = field
    merged = dict(headers)
    merged.update(extra_headers or {})
    return json_response(payload, status=status, headers=merged)


def handle(app, req: Request) -> Response | None:
    path = req.path
    if path not in ("/api/v1/thread", "/api/v1/comments"):
        return None
    settings = app.settings.all()
    headers = _cors(req, settings)

    if req.method == "OPTIONS":
        if "Access-Control-Allow-Origin" not in headers:
            return Response(b"", status=403, headers={"Vary": "Origin"})
        headers.update({
            "Access-Control-Allow-Methods": "GET, POST",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
        })
        return Response(b"", status=204, headers=headers)

    try:
        if path == "/api/v1/thread" and req.method == "GET":
            return get_thread(app, req, settings, headers)
        if path == "/api/v1/comments" and req.method == "POST":
            return post_comment(app, req, settings, headers)
    except HTTPError as exc:
        return _error(exc.status, exc.code, exc.message, headers, extra_headers=exc.headers)
    return _error(405, "method_not_allowed", "Method not allowed.", headers,
                  extra_headers={"Allow": "GET" if path.endswith("thread") else "POST"})


def form_config(app, thread: str, settings: dict) -> dict:
    return {
        "token": app.form_tokens.issue(thread),
        "maxName": MAX_NAME,
        "maxBody": settings["max_body_chars"],
        "email": settings["ask_email"],
        "formatting": settings["formatting"],
        "links": settings["formatting"] == "basic" and settings["linkify"],
    }


def get_thread(app, req: Request, settings: dict, headers: dict) -> Response:
    thread = valid_thread(req.arg("id"))
    if thread is None:
        return _error(400, "invalid_thread", "Missing or invalid thread id.", headers)
    order = "ASC" if settings["thread_order"] == "oldest" else "DESC"
    rows = app.db.conn().execute(
        f"SELECT public_id, author, body, created_at FROM comments "
        f"WHERE thread = ? AND status = 'approved' ORDER BY created_at {order}, id {order} LIMIT ?",
        (thread, MAX_THREAD_COMMENTS),
    ).fetchall()
    data = {
        "thread": thread,
        "open": settings["comments_open"],
        "order": settings["thread_order"],
        "count": len(rows),
        "comments": [public_comment(r, settings) for r in rows],
        "form": form_config(app, thread, settings) if settings["comments_open"] else None,
    }
    return json_response(data, headers=headers)


def post_comment(app, req: Request, settings: dict, headers: dict) -> Response:
    now = time.time()
    if "Access-Control-Allow-Origin" not in headers:
        app.db.bump("origin_blocked")
        return _error(403, "origin_not_allowed",
                      "This site is not allowed to post comments here.", headers)
    data = req.json(MAX_REQUEST_BYTES)
    if not isinstance(data, dict):
        return _error(400, "invalid_json", "Expected a JSON object.", headers)
    if not settings["comments_open"]:
        return _error(403, "comments_closed", "Comments are closed.", headers)

    # Honeypot: a field people never see. Pretend success so bots learn nothing.
    if str(data.get("website") or "").strip():
        app.db.bump("honeypot")
        return json_response({"status": "pending"}, status=202, headers=headers)

    thread = valid_thread(data.get("thread"))
    if thread is None:
        return _error(400, "invalid_thread", "Missing or invalid thread id.", headers)

    author = textmod.clean(str(data.get("author") or ""), multiline=False)
    if not textmod.has_visible_text(author):
        return _error(400, "name_required", "Please enter a name.", headers, "author")
    if len(author) > MAX_NAME:
        return _error(400, "name_too_long", f"Please keep your name under {MAX_NAME} characters.",
                      headers, "author")

    email = None
    if settings["ask_email"]:
        email = textmod.clean(str(data.get("email") or ""), multiline=False) or None
        if email and (len(email) > MAX_EMAIL or not _EMAIL.match(email)):
            return _error(400, "email_invalid", "That email address does not look right.",
                          headers, "email")

    body = textmod.clean(str(data.get("body") or ""), multiline=True)
    if not textmod.has_visible_text(body):
        return _error(400, "body_required", "Please write a comment.", headers, "body")
    if len(body) > settings["max_body_chars"]:
        return _error(400, "body_too_long",
                      f"Please keep your comment under {settings['max_body_chars']} characters.",
                      headers, "body")

    token = str(data.get("token") or "")
    problem = app.form_tokens.check(token, thread, settings["min_seconds"], now)
    if problem == "too_fast":
        app.db.bump("too_fast")
        return _error(400, "too_fast", "That was quick! Please wait a few seconds and send again.", headers)
    if problem:
        return _error(400, "form_expired", "The form has expired. Please reload the page.", headers)

    ipk = security.ip_key(app.secret, req.remote_addr)
    wait = max(app.limiter.hit("ip:" + ipk, settings["rate_per_ip"], 600, now, record=False),
               app.limiter.hit("global", settings["rate_global"], 3600, now, record=False))
    if wait:
        app.db.bump("rate_limited")
        return _error(429, "rate_limited", "Too many comments in a short time. Please try again later.",
                      headers, extra_headers={"Retry-After": str(int(wait) + 1)})

    page_url = None
    raw_page = data.get("page")
    if isinstance(raw_page, str) and len(raw_page) <= 2000:
        parts = urlsplit(raw_page)
        if parts.scheme in ("http", "https") and normalize_origin(
                f"{parts.scheme}://{parts.netloc}") == normalize_origin(req.header("Origin") or ""):
            page_url = raw_page

    conn = app.db.conn()
    flags = moderation.basic_flags(settings, conn, author, body, email, now)
    use_laya = moderation.wants_laya(settings)
    pid = security.public_id()
    row = {
        "public_id": pid, "thread": thread, "page_url": page_url, "author": author,
        "email": email, "body": body, "body_hash": moderation.body_hash(body),
        "flags": json.dumps(flags), "created_at": int(now), "ip_key": ipk,
    }

    if use_laya:
        # Store first, then ask Laya: a crash or hang cannot lose the comment.
        with conn:
            conn.execute(
                "INSERT INTO comments (public_id, thread, page_url, author, email, body, body_hash, "
                "status, reason, flags, created_at, ip_key) VALUES (:public_id, :thread, :page_url, "
                ":author, :email, :body, :body_hash, 'pending', 'Received; waiting for Laya.', "
                ":flags, :created_at, :ip_key)", row)
        if settings["laya_enabled"]:
            result = app.laya.score(settings, author, body)
        else:
            from .laya import LayaResult
            result = LayaResult(ok=False, error="Laya is turned off")
        if not result.ok and settings["laya_enabled"]:
            app.db.bump("laya_error")
        decision = moderation.decide(settings, flags, result)
        with conn:
            conn.execute(
                "UPDATE comments SET status = ?, reason = ?, decided_by = ?, decided_at = ?, "
                "laya_status = ?, laya_score = ?, laya_detail = ? WHERE public_id = ?",
                (decision.status, decision.reason, decision.decided_by,
                 int(time.time()) if decision.decided_by else None,
                 ("scored" if result.ok else "error") if settings["laya_enabled"] else None,
                 result.score, result.detail_json() if settings["laya_enabled"] else None, pid),
            )
    else:
        decision = moderation.decide(settings, flags, None)
        row.update(status=decision.status, reason=decision.reason, decided_by=decision.decided_by,
                   decided_at=int(now) if decision.decided_by else None)
        with conn:
            conn.execute(
                "INSERT INTO comments (public_id, thread, page_url, author, email, body, body_hash, "
                "status, reason, decided_by, decided_at, flags, created_at, ip_key) VALUES "
                "(:public_id, :thread, :page_url, :author, :email, :body, :body_hash, :status, "
                ":reason, :decided_by, :decided_at, :flags, :created_at, :ip_key)", row)

    app.form_tokens.consume(token, now)
    app.limiter.hit("ip:" + ipk, settings["rate_per_ip"], 600, now)
    app.limiter.hit("global", settings["rate_global"], 3600, now)
    app.db.bump("received")
    app.db.bump({"approved": "published", "pending": "held", "rejected": "rejected_auto"}[decision.status])

    next_token = app.form_tokens.issue(thread)
    if decision.status == "approved":
        saved = conn.execute("SELECT public_id, author, body, created_at FROM comments "
                             "WHERE public_id = ?", (pid,)).fetchone()
        return json_response({"status": "published", "comment": public_comment(saved, settings),
                              "token": next_token}, status=201, headers=headers)
    # Rejected comments get the same answer as pending ones: spammers learn nothing.
    return json_response({"status": "pending", "token": next_token}, status=202, headers=headers)
