"""The admin dashboard.

Server-rendered HTML, usable without JavaScript. A tiny same-origin script adds
"select all" and a confirmation before permanent deletion. Every page is served
with a strict Content-Security-Policy (see app.py), every form carries a CSRF
token, and every POST must come from the dashboard's own origin.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse

from . import security, settings as settings_mod
from .api import iso
from .web import HTTPError, Markup, Request, Response, escape, join, redirect, render

SESSION_DAYS = 14
PAGE_SIZE = 50
STATUS_LABEL = {"pending": "Pending", "approved": "Published", "rejected": "Rejected"}


# ---------------------------------------------------------------------------
# sessions, CSRF, origin checks
# ---------------------------------------------------------------------------

def cookie_name(app) -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure,
    # host-only and Path=/, which rules out injection from sibling subdomains.
    return "afterword_session" if app.cfg.dev else "__Host-afterword"


def current_session(app, req: Request):
    raw = req.cookies().get(cookie_name(app))
    if not raw or len(raw) > 200:
        return None
    row = app.db.conn().execute(
        "SELECT token_hash, csrf, expires_at FROM sessions WHERE token_hash = ?",
        (security.sha256_hex(raw),),
    ).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return row


def start_session(app, resp: Response) -> None:
    raw = security.token(32)
    now = int(time.time())
    conn = app.db.conn()
    with conn:
        conn.execute("INSERT INTO sessions (token_hash, csrf, created_at, expires_at) VALUES (?, ?, ?, ?)",
                     (security.sha256_hex(raw), security.token(24), now, now + SESSION_DAYS * 86400))
    resp.set_cookie(cookie_name(app), raw, max_age=SESSION_DAYS * 86400, secure=not app.cfg.dev)


def check_origin(app, req: Request) -> None:
    """Reject cross-site form posts even if a CSRF token were somehow known."""
    site = req.header("Sec-Fetch-Site")
    if site and site not in ("same-origin", "none"):
        raise HTTPError(403, "cross_site", "This form was submitted from another site.")
    expected = app.cfg.public_origin or (req.origin)
    origin = req.header("Origin")
    if origin and origin != "null":
        if origin.lower() != expected:
            raise HTTPError(403, "origin_mismatch",
                            f"This form was sent from {origin}, but the dashboard expects {expected}. "
                            "If you use a reverse proxy, set AFTERWORD_PUBLIC_URL.")
        return
    if origin == "null":
        raise HTTPError(403, "origin_mismatch", "This form was sent from an opaque origin.")
    referer = req.header("Referer")
    if referer:
        parts = urllib.parse.urlsplit(referer)
        if f"{parts.scheme}://{parts.netloc}".lower() != expected:
            raise HTTPError(403, "origin_mismatch", "This form was submitted from another site.")


def check_csrf(req: Request, session) -> None:
    sent = req.field("csrf")
    if not sent or not hmac.compare_digest(sent, session["csrf"]):
        raise HTTPError(403, "csrf", "The form was stale. Reload the page and try again.")


def csrf_field(session) -> Markup:
    return render('<input type="hidden" name="csrf" value="{v}">', v=session["csrf"])


def flash(app, session, kind: str, message: str) -> None:
    app.flashes[session["token_hash"]] = (kind, message)


# ---------------------------------------------------------------------------
# page chrome
# ---------------------------------------------------------------------------

NAV = [("comments", "/admin/comments", "Comments"), ("settings", "/admin/settings", "Settings"),
       ("pseudonyms", "/admin/pseudonyms", "Pseudonyms"), ("laya", "/admin/laya", "Laya"),
       ("embed", "/admin/embed", "Add to your blog"), ("account", "/admin/account", "Account")]


def _pseudonyms_in_use(app) -> bool:
    return app.settings.get("pseudonyms") or app.db.conn().execute(
        "SELECT EXISTS (SELECT 1 FROM pseudonyms)").fetchone()[0] == 1


def page(app, req: Request, title: str, body: Markup, *, session=None, current: str = "",
         refresh: int | None = None, status: int = 200) -> Response:
    nav = Markup("")
    if session is not None:
        pending = app.db.conn().execute(
            "SELECT COUNT(*) FROM comments WHERE status = 'pending'").fetchone()[0]
        links = []
        show_pseudonyms = current == "pseudonyms" or _pseudonyms_in_use(app)
        for key, path, label in NAV:
            if key == "pseudonyms" and not show_pseudonyms:
                continue
            count = render(' <span class="count">{n}</span>', n=pending) if key == "comments" and pending else ""
            links.append(render('<a href="{href}"{cur}>{label}{count}</a>', href=req.url(path),
                                label=label, count=count,
                                cur=Markup(' aria-current="page"') if key == current else ""))
        nav = render(
            '<nav class="nav" aria-label="Dashboard">{links}</nav>'
            '<form method="post" action="{out}" class="signout">{csrf}'
            '<button type="submit" class="quiet">Sign out</button></form>',
            links=join(links), out=req.url("/admin/logout"), csrf=csrf_field(session))
    note = Markup("")
    if session is not None:
        message = app.flashes.pop(session["token_hash"], None)
        if message:
            note = render('<p class="flash flash-{k}" role="status">{m}</p>', k=message[0], m=message[1])
    html = render(
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{refresh}<title>{title} · Afterword</title>
<link rel="stylesheet" href="{css}">
<script src="{js}" defer></script>
</head>
<body>
<header class="top"><div class="top-inner"><a class="brand" href="{home}">Afterword</a>{nav}</div></header>
<main class="main">
{note}
{body}
</main>
</body>
</html>""",
        refresh=render('<meta http-equiv="refresh" content="{s}">\n', s=refresh) if refresh else "",
        title=title, css=req.url("/admin/static/admin.css?v=" + app.static_version),
        js=req.url("/admin/static/admin.js?v=" + app.static_version), home=req.url("/admin"),
        nav=nav, note=note, body=body)
    return Response(html, status=status)


def ago(ts: int | None, now: float | None = None) -> str:
    if not ts:
        return ""
    delta = int((now or time.time()) - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60} min ago"
    if delta < 86400:
        hours = delta // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if delta < 14 * 86400:
        days = delta // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    t = time.gmtime(ts)
    return f"{t.tm_mday} {time.strftime('%b %Y', t)}"  # "%-d" is not portable


# ---------------------------------------------------------------------------
# form controls generated from the settings spec
# ---------------------------------------------------------------------------

class Form:
    def __init__(self, values: dict, errors: dict | None = None):
        self.values = values
        self.errors = errors or {}

    def _err(self, key: str) -> Markup:
        if key in self.errors:
            return render('<p class="error" id="{k}-error">{m}</p>', k=key, m=self.errors[key])
        return Markup("")

    def _help(self, key: str, text: str) -> Markup:
        return render('<p class="help" id="{k}-help">{t}</p>', k=key, t=text) if text else Markup("")

    def _described(self, key: str, help_text: str) -> Markup:
        ids = []
        if help_text:
            ids.append(f"{key}-help")
        if key in self.errors:
            ids.append(f"{key}-error")
        return render(' aria-describedby="{i}"', i=" ".join(ids)) if ids else Markup("")

    def checkbox(self, key: str, label: str, help_text: str = "") -> Markup:
        return render(
            '<div class="field check"><input type="checkbox" id="{k}" name="{k}" value="1"{c}{d}>'
            '<label for="{k}">{label}</label>{help}{err}</div>',
            k=key, c=Markup(" checked") if self.values.get(key) else "", label=label,
            help=self._help(key, help_text), err=self._err(key), d=self._described(key, help_text))

    def number(self, key: str, label: str, help_text: str = "", step: str = "1", unit: str = "") -> Markup:
        opt = settings_mod.BY_KEY[key]
        return render(
            '<div class="field"><label for="{k}">{label}</label>'
            '<span class="with-unit"><input type="number" id="{k}" name="{k}" value="{v}" step="{step}" '
            'min="{mn}" max="{mx}" inputmode="decimal"{d}>{unit}</span>{help}{err}</div>',
            k=key, label=label, v=self.values.get(key), step=step,
            mn="" if opt.min is None else f"{opt.min:g}", mx="" if opt.max is None else f"{opt.max:g}",
            unit=render('<span class="unit">{u}</span>', u=unit) if unit else "",
            help=self._help(key, help_text), err=self._err(key), d=self._described(key, help_text))

    def text(self, key: str, label: str, help_text: str = "", kind: str = "text",
             placeholder: str = "", value=None) -> Markup:
        return render(
            '<div class="field"><label for="{k}">{label}</label>'
            '<input type="{kind}" id="{k}" name="{k}" value="{v}" placeholder="{ph}" autocomplete="off"{d}>'
            '{help}{err}</div>',
            k=key, label=label, kind=kind, v=self.values.get(key) if value is None else value,
            ph=placeholder, help=self._help(key, help_text), err=self._err(key),
            d=self._described(key, help_text))

    def textarea(self, key: str, label: str, help_text: str = "", rows: int = 4, value=None,
                 placeholder: str = "") -> Markup:
        v = self.values.get(key) if value is None else value
        if isinstance(v, list):
            v = "\n".join(v)
        return render(
            '<div class="field"><label for="{k}">{label}</label>'
            '<textarea id="{k}" name="{k}" rows="{r}" placeholder="{ph}"{d}>{v}</textarea>{help}{err}</div>',
            k=key, label=label, r=rows, v=v, ph=placeholder, help=self._help(key, help_text),
            err=self._err(key), d=self._described(key, help_text))

    def radios(self, key: str, legend: str, options: list[tuple[str, str, str]]) -> Markup:
        items = []
        for value, label, help_text in options:
            rid = f"{key}-{value}"
            items.append(render(
                '<div class="radio"><input type="radio" id="{rid}" name="{k}" value="{v}"{c}>'
                '<label for="{rid}"><strong>{label}</strong>{help}</label></div>',
                rid=rid, k=key, v=value, label=label,
                c=Markup(" checked") if self.values.get(key) == value else "",
                help=render('<span class="help">{t}</span>', t=help_text) if help_text else ""))
        return render('<fieldset class="field radios"><legend>{legend}</legend>{items}{err}</fieldset>',
                      legend=legend, items=join(items), err=self._err(key))


def read_form(req: Request, keys: list[str]) -> dict:
    """Map submitted fields onto setting keys; unchecked boxes mean False."""
    values = {}
    for key in keys:
        opt = settings_mod.BY_KEY[key]
        if opt.kind == "bool":
            values[key] = req.field(key) == "1"
        elif opt.kind == "secret" and req.field(key) == "":
            continue  # empty secret field means "keep the stored one"
        else:
            values[key] = req.field(key)
    return values


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

def handle(app, req: Request) -> Response | None:
    path = req.path
    if not (path == "/admin" or path.startswith("/admin/")):
        return None
    if path.startswith("/admin/static/"):
        return None  # served by app.py

    has_password = bool(app.db.meta_get("admin_password"))
    if path == "/admin/setup":
        return setup_page(app, req, has_password)
    if not has_password:
        return redirect(req.url("/admin/setup"))
    if path == "/admin/login":
        return login_page(app, req)

    session = current_session(app, req)
    if session is None:
        return redirect(req.url("/admin/login"))
    if req.method == "POST":
        check_origin(app, req)
        check_csrf(req, session)

    routes = {
        "/admin": lambda: redirect(req.url("/admin/comments")),
        "/admin/logout": lambda: logout(app, req, session),
        "/admin/comments": lambda: comments_page(app, req, session),
        "/admin/settings": lambda: settings_page(app, req, session),
        "/admin/pseudonyms": lambda: pseudonyms_page(app, req, session),
        "/admin/laya": lambda: laya_page(app, req, session),
        "/admin/laya/service": lambda: laya_service(app, req, session),
        "/admin/laya/test": lambda: laya_page(app, req, session, test=True),
        "/admin/embed": lambda: embed_page(app, req, session),
        "/admin/account": lambda: account_page(app, req, session),
        "/admin/backup": lambda: backup(app, req, session),
    }
    handler = routes.get(path.rstrip("/") or "/admin")
    if handler is None:
        return page(app, req, "Not found", Markup("<h1>Not found</h1>"), session=session, status=404)
    return handler()


# ---------------------------------------------------------------------------
# first run, login, logout
# ---------------------------------------------------------------------------

def setup_page(app, req: Request, has_password: bool) -> Response:
    if has_password:
        return redirect(req.url("/admin/login"))
    error = ""
    if req.method == "POST":
        check_origin(app, req)
        wait = app.limiter.hit("setup:" + security.ip_key(app.secret, req.remote_addr), 10, 900)
        code = req.field("code").strip().upper()
        stored = app.db.meta_get("setup_code_hash")
        expires = int(app.db.meta_get("setup_code_expires") or 0)
        password, confirm = req.field("password"), req.field("confirm")
        if wait:
            error = "Too many attempts. Wait a few minutes and try again."
        elif not stored or expires < time.time():
            error = "The setup code has expired. Restart the server to get a new one."
        elif not hmac.compare_digest(security.sha256_hex(code), stored):
            error = "That setup code is not right. It is printed in the server log."
        elif security.password_problem(password):
            error = security.password_problem(password)
        elif password != confirm:
            error = "The two passwords are different."
        else:
            app.db.meta_set("admin_password", security.hash_password(password))
            app.db.meta_set("setup_code_hash", None)
            app.db.meta_set("setup_code_expires", None)
            resp = redirect(req.url("/admin/embed"))
            start_session(app, resp)
            return resp
    insecure = _insecure_warning(app, req)
    body = render(
        """<section class="narrow">
<h1>Set up Afterword</h1>
<p>Choose the password for this dashboard. The setup code is printed in the server log when Afterword starts.</p>
{insecure}{error}
<form method="post" class="stack">
<div class="field"><label for="code">Setup code</label>
<input id="code" name="code" value="{code}" autocomplete="off" autocapitalize="characters" required></div>
<div class="field"><label for="password">New password</label>
<input id="password" name="password" type="password" autocomplete="new-password" minlength="{min}" required>
<p class="help">At least {min} characters. A passphrase of a few words works well.</p></div>
<div class="field"><label for="confirm">Repeat password</label>
<input id="confirm" name="confirm" type="password" autocomplete="new-password" required></div>
<button type="submit" class="primary">Save password and continue</button>
</form></section>""",
        insecure=insecure, error=render('<p class="flash flash-error" role="alert">{e}</p>', e=error) if error else "",
        code=req.field("code") or req.arg("code"), min=security.MIN_PASSWORD_LENGTH)
    return page(app, req, "Set up", body)


def _insecure_warning(app, req: Request) -> Markup:
    host = req.host.split(":")[0]
    if req.scheme == "https" or app.cfg.dev or host in ("localhost", "127.0.0.1", "[::1]"):
        return Markup("")
    return Markup('<p class="flash flash-error" role="alert">This page was opened over plain http, so '
                  'your browser will not keep the sign-in cookie. Put Afterword behind HTTPS '
                  '(see the README), or set AFTERWORD_DEV=1 for local testing only.</p>')


def login_page(app, req: Request) -> Response:
    if current_session(app, req) is not None:
        return redirect(req.url("/admin/comments"))
    error = ""
    if req.method == "POST":
        check_origin(app, req)
        ipk = security.ip_key(app.secret, req.remote_addr)
        blocked = max(app.limiter.hit("login:" + ipk, 10, 900, record=False),
                      app.limiter.hit("login", 60, 3600, record=False))
        if blocked:
            error = f"Too many attempts. Try again in {int(blocked / 60) + 1} minutes."
        elif security.verify_password(req.field("password"), app.db.meta_get("admin_password")):
            resp = redirect(req.url("/admin/comments"))
            start_session(app, resp)
            return resp
        else:
            app.limiter.hit("login:" + ipk, 10, 900)
            app.limiter.hit("login", 60, 3600)
            error = "That password is not right."
    body = render(
        """<section class="narrow">
<h1>Sign in</h1>
{insecure}{error}
<form method="post" class="stack">
<div class="field"><label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required autofocus></div>
<button type="submit" class="primary">Sign in</button>
</form>
<p class="help">Forgot it? Run <code>python -m afterword set-password</code> on the server.</p>
</section>""",
        insecure=_insecure_warning(app, req),
        error=render('<p class="flash flash-error" role="alert">{e}</p>', e=error) if error else "")
    return page(app, req, "Sign in", body, status=200 if not error else 401)


def logout(app, req: Request, session) -> Response:
    if req.method != "POST":
        return redirect(req.url("/admin/comments"))
    conn = app.db.conn()
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
    resp = redirect(req.url("/admin/login"))
    resp.set_cookie(cookie_name(app), "", max_age=0, secure=not app.cfg.dev)
    return resp


# ---------------------------------------------------------------------------
# comments
# ---------------------------------------------------------------------------

FILTER_KEYS = ("status", "thread", "q", "sort", "sender", "pseudonym", "page")


def _list_url(req: Request, **params) -> str:
    clean = {k: v for k, v in params.items() if k in FILTER_KEYS and v not in ("", None)}
    return req.url("/admin/comments") + ("?" + urllib.parse.urlencode(clean) if clean else "")


def _current_filters(req: Request, source=None) -> dict:
    get = source or req.arg
    status = get("status") or "pending"
    if status not in ("pending", "approved", "rejected", "all"):
        status = "pending"
    sort = get("sort") or "newest"
    if sort not in ("newest", "oldest", "spam"):
        sort = "newest"
    try:
        page_no = max(1, min(10_000, int(get("page") or 1)))
    except ValueError:
        page_no = 1
    pseudonym = get("pseudonym")[:12]
    return {"status": status, "thread": get("thread")[:300], "q": get("q")[:200],
            "sort": sort, "sender": get("sender")[:16],
            "pseudonym": pseudonym if pseudonym.isascii() and pseudonym.isdigit() else "",
            "page": page_no}


def comments_page(app, req: Request, session) -> Response:
    if req.method == "POST":
        return comment_action(app, req, session)
    s = app.settings.all()
    f = _current_filters(req)
    conn = app.db.conn()
    where, params = [], []
    if f["status"] != "all":
        where.append("status = ?")
        params.append(f["status"])
    if f["thread"]:
        where.append("thread = ?")
        params.append(f["thread"])
    if f["sender"]:
        where.append("ip_key = ?")
        params.append(f["sender"])
    if f["pseudonym"]:
        where.append("pseudonym_id = ?")
        params.append(int(f["pseudonym"]))
    if f["q"]:
        like = "%" + f["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(body LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\')")
        params += [like, like, like]
    order = {"newest": "created_at DESC, id DESC", "oldest": "created_at ASC, id ASC",
             "spam": "laya_score IS NULL, laya_score DESC, created_at DESC"}[f["sort"]]
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM comments {sql_where}", params).fetchone()[0]
    rows = conn.execute(f"SELECT * FROM comments {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                        params + [PAGE_SIZE, (f["page"] - 1) * PAGE_SIZE]).fetchall()
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM comments GROUP BY status").fetchall())

    tabs = []
    for status, label in (("pending", "Pending"), ("approved", "Published"),
                          ("rejected", "Rejected"), ("all", "All")):
        n = sum(counts.values()) if status == "all" else counts.get(status, 0)
        tabs.append(render('<a href="{href}"{cur}>{label} <span class="count">{n}</span></a>',
                           href=_list_url(req, status=status, sort=f["sort"]), label=label, n=n,
                           cur=Markup(' aria-current="page"') if f["status"] == status else ""))

    now = time.time()
    items = [_comment_item(req, r, s, now) for r in rows]
    if not items:
        empty = {"pending": "Nothing is waiting for review.",
                 "approved": "No published comments yet.",
                 "rejected": "Nothing has been rejected."}.get(f["status"], "No comments match.")
        if f["thread"] or f["q"] or f["sender"] or f["pseudonym"]:
            empty = "No comments match these filters."
        items = [render('<li class="empty">{t}</li>', t=empty)]

    filters_note = []
    if f["thread"]:
        filters_note.append(render('post <code>{t}</code>', t=f["thread"]))
    if f["sender"]:
        filters_note.append(render('sender <code>{t}</code>', t=f["sender"]))
    if f["pseudonym"]:
        held = conn.execute("SELECT name FROM pseudonyms WHERE id = ?", (int(f["pseudonym"]),)).fetchone()
        filters_note.append(render('pseudonym <strong>{n}</strong>', n=held[0]) if held
                            else Markup("a pseudonym that has been released"))
    clear = render(' <a href="{h}">Show everything</a>', h=_list_url(req, status=f["status"])) \
        if filters_note else ""

    pages = Markup("")
    if total > PAGE_SIZE:
        prev_link = render('<a href="{h}">Newer</a>', h=_list_url(req, **{**f, "page": f["page"] - 1})) \
            if f["page"] > 1 else ""
        next_link = render('<a href="{h}">Older</a>', h=_list_url(req, **{**f, "page": f["page"] + 1})) \
            if f["page"] * PAGE_SIZE < total else ""
        pages = render('<nav class="pager" aria-label="Pages">{p}<span>Page {n} of {m}</span>{x}</nav>',
                       p=prev_link, x=next_link, n=f["page"], m=(total + PAGE_SIZE - 1) // PAGE_SIZE)

    notices = []
    if not s["site_origins"]:
        notices.append(render('<p class="flash flash-error">No blog address is set, so the widget cannot '
                              'load or post comments yet. <a href="{h}">Add your blog’s address</a>.</p>',
                              h=req.url("/admin/settings")))
    back = urllib.parse.urlencode({k: v for k, v in f.items() if v not in ("", None)})
    body = render(
        """<h1>Comments</h1>
{notices}
<nav class="tabs" aria-label="Status">{tabs}</nav>
<form method="get" class="filters" role="search">
<input type="hidden" name="status" value="{status}">
<label for="q" class="visually-hidden">Search</label>
<input type="search" id="q" name="q" value="{q}" placeholder="Search names, emails and text">
<label for="sort">Sort</label>
<select id="sort" name="sort">{sort_opts}</select>
<button type="submit">Show</button>
</form>
{filters_line}
<form method="post" id="bulk" class="bulk">{csrf}
<input type="hidden" name="back" value="{back}">
<div class="bulkbar">
<label class="check-all"><input type="checkbox" data-select-all> Select all on this page</label>
<label for="action" class="visually-hidden">Action</label>
<select id="action" name="action">
<option value="approve">Publish</option><option value="reject">Reject</option>
<option value="pending">Move to pending</option><option value="rescore">Ask Laya again</option>
<option value="delete">Delete permanently</option>
</select>
<button type="submit" name="bulk" value="1">Apply to selected</button>
</div>
<ol class="comments">{items}</ol>
</form>
{pages}""",
        notices=join(notices), tabs=join(tabs), status=f["status"], q=f["q"],
        sort_opts=join(render('<option value="{v}"{sel}>{l}</option>', v=v, l=l,
                              sel=Markup(" selected") if f["sort"] == v else "")
                       for v, l in (("newest", "Newest first"), ("oldest", "Oldest first"),
                                    ("spam", "Most likely spam first"))),
        filters_line=render('<p class="filters-note">Showing only {what}.{clear}</p>',
                            what=join(filters_note, " and "), clear=clear) if filters_note else "",
        csrf=csrf_field(session), back=back, items=join(items), pages=pages)
    return page(app, req, "Comments", body, session=session, current="comments")


def _laya_badge(row, s: dict) -> Markup:
    if row["laya_status"] == "scored" and row["laya_score"] is not None:
        score = row["laya_score"]
        likely = score >= s["laya_flag_at"]
        detail = json.loads(row["laya_detail"] or "{}")
        extra = []
        if detail.get("model"):
            extra.append(detail["model"])
        if detail.get("latency_ms") is not None:
            extra.append(f"{detail['latency_ms']} ms")
        return render(
            '<p class="laya{cls}"><span>Laya spam score</span> '
            '<meter min="0" max="1" low="{low}" high="{high}" optimum="0" value="{v}">{pct}</meter> '
            '<strong>{v2}</strong>{likely}{extra}</p>',
            cls=" laya-likely" if likely else "", low=f"{s['laya_approve_below']:.2f}",
            high=f"{s['laya_flag_at']:.2f}", v=f"{score:.4f}", v2=f"{score:.2f}", pct=f"{score:.0%}",
            likely=Markup(' <span class="tag tag-spam">likely spam</span>') if likely else "",
            extra=render(' <span class="muted">({e})</span>', e=", ".join(extra)) if extra else "")
    if row["laya_status"] == "error":
        detail = json.loads(row["laya_detail"] or "{}")
        return render('<p class="laya muted">Laya did not score this: {e}</p>', e=detail.get("error", "error"))
    return Markup("")


def _comment_item(req: Request, row, s: dict, now: float) -> Markup:
    from .moderation import describe_flag
    flags = json.loads(row["flags"] or "[]")
    pid = row["public_id"]
    buttons = []
    if row["status"] != "approved":
        buttons.append(("approve", "Publish"))
    if row["status"] != "rejected":
        buttons.append(("reject", "Reject"))
    if row["status"] != "pending":
        buttons.append(("pending", "Move to pending"))
    label = urllib.parse.urlsplit(row["page_url"]).path if row["page_url"] else row["thread"]
    thread_link = render('<a href="{h}" title="All comments on this post (thread {t})">{label}</a>',
                         h=_list_url(req, status="all", thread=row["thread"]), t=row["thread"],
                         label=label or row["thread"])
    page_link = render(' <a href="{h}" rel="noreferrer noopener" target="_blank">open page</a>',
                       h=row["page_url"]) if row["page_url"] else ""
    sender = render(' <a class="sender" href="{h}" title="All comments from the same network">sender {k}</a>',
                    h=_list_url(req, status="all", sender=row["ip_key"]), k=row["ip_key"][:6]) \
        if row["ip_key"] else ""
    pseudonym = render(' <a class="tag tag-pseudonym" href="{h}" title="Posted with this pseudonym’s key. '
                       'All comments from the same holder">✓ pseudonym</a>',
                       h=_list_url(req, status="all", pseudonym=str(row["pseudonym_id"]))) \
        if row["pseudonym_id"] is not None else ""
    likely = row["laya_status"] == "scored" and row["laya_score"] is not None \
        and row["laya_score"] >= s["laya_flag_at"]
    return render(
        """<li class="comment status-{status}{likely}" id="c-{pid}">
<input type="checkbox" name="id" value="{pid}" id="sel-{pid}" class="select" aria-label="Select comment by {author}">
<div class="comment-main">
<p class="comment-head"><strong class="author">{author}</strong>{pseudonym}{email}
<span class="when" title="{full}">{when}</span></p>
<p class="comment-where">On {thread}{page_link}{sender}</p>
<div class="comment-text">{body}</div>
<p class="why"><span class="status status-{status}">{label}</span> {reason}</p>
{flags}{laya}
<div class="actions">{buttons}
<button type="submit" name="one" value="delete:{pid}" class="danger" data-confirm="Delete this comment permanently?">Delete</button>
</div>
</div>
</li>""",
        status=row["status"], likely=" is-likely-spam" if likely else "", pid=pid,
        author=row["author"], pseudonym=pseudonym,
        email=render(' <span class="email">{e}</span>', e=row["email"]) if row["email"] else "",
        full=iso(row["created_at"]).replace("T", " ").replace("Z", " UTC"),
        when=ago(row["created_at"], now), thread=thread_link, page_link=page_link, sender=sender,
        body=row["body"], label=STATUS_LABEL[row["status"]], reason=row["reason"],
        flags=render('<ul class="flags">{f}</ul>',
                     f=join(render("<li>{x}</li>", x=describe_flag(x, s)) for x in flags)) if flags else "",
        laya=_laya_badge(row, s),
        buttons=join(render('<button type="submit" name="one" value="{a}:{pid}">{l}</button>',
                            a=a, pid=pid, l=l) for a, l in buttons))


def comment_action(app, req: Request, session) -> Response:
    back = urllib.parse.parse_qs(req.field("back"))
    f = _current_filters(req, source=lambda k: (back.get(k) or [""])[0])
    target = _list_url(req, **f)
    one = req.field("one")
    if one:
        action, _, pid = one.partition(":")
        ids = [pid]
    else:
        action = req.field("action")
        ids = req.fields("id")[:500]
    ids = [i for i in ids if i and len(i) <= 32 and i.isalnum()]
    if action not in ("approve", "reject", "pending", "delete", "rescore") or not ids:
        flash(app, session, "info", "Select at least one comment first.")
        return redirect(target)
    conn = app.db.conn()
    marks = ",".join("?" * len(ids))
    now = int(time.time())
    if action == "delete":
        with conn:
            n = conn.execute(f"DELETE FROM comments WHERE public_id IN ({marks})", ids).rowcount
        flash(app, session, "ok", f"Deleted {n} comment{'s' if n != 1 else ''} permanently.")
    elif action == "rescore":
        s = app.settings.all()
        rows = conn.execute(f"SELECT public_id, author, body FROM comments WHERE public_id IN ({marks})",
                            ids).fetchall()[:50]
        scored, failed = 0, None
        for row in rows:
            result = app.laya.score(s, row["author"], row["body"], bypass_cooldown=True)
            with conn:
                conn.execute("UPDATE comments SET laya_status = ?, laya_score = ?, laya_detail = ? "
                             "WHERE public_id = ?",
                             ("scored" if result.ok else "error", result.score, result.detail_json(),
                              row["public_id"]))
            if result.ok:
                scored += 1
            else:
                failed = result.error
                break
        if failed:
            flash(app, session, "error", f"Laya scored {scored} before failing: {failed}")
        else:
            flash(app, session, "ok", f"Laya scored {scored} comment{'s' if scored != 1 else ''}. "
                                      "Their status did not change.")
    else:
        status = {"approve": "approved", "reject": "rejected", "pending": "pending"}[action]
        reason = {"approved": "Published by you.", "rejected": "Rejected by you.",
                  "pending": "Moved back to pending by you."}[status]
        with conn:
            n = conn.execute(
                f"UPDATE comments SET status = ?, reason = ?, decided_by = ?, decided_at = ? "
                f"WHERE public_id IN ({marks})",
                [status, reason, None if status == "pending" else "admin", now] + ids).rowcount
        verb = {"approved": "Published", "rejected": "Rejected", "pending": "Moved to pending"}[status]
        flash(app, session, "ok", f"{verb} {n} comment{'s' if n != 1 else ''}.")
    return redirect(target)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

SETTINGS_KEYS = ["site_origins", "comments_open", "moderation_mode", "automatic_decider",
                 "formatting", "linkify", "ask_email", "pseudonyms", "thread_order", "max_body_chars",
                 "min_seconds", "max_links", "hold_duplicates", "blocked_terms", "rate_per_ip",
                 "rate_global", "purge_rejected_days"]


def settings_page(app, req: Request, session) -> Response:
    errors: dict = {}
    values = app.settings.all()
    if req.method == "POST":
        submitted = read_form(req, SETTINGS_KEYS)
        errors = app.settings.update(submitted)
        if not errors:
            flash(app, session, "ok", "Settings saved.")
            return redirect(req.url("/admin/settings"))
        values = {**values, **submitted}
    s = app.settings.all()
    form = Form(values, errors)
    warnings = []
    if s["moderation_mode"] == "assisted" and not s["laya_enabled"]:
        warnings.append("Assisted moderation uses Laya to flag likely spam, but Laya is off, "
                        "so it currently works like manual moderation.")
    if s["moderation_mode"] == "automatic" and s["automatic_decider"] == "laya" and not s["laya_enabled"]:
        warnings.append("Laya decides in automatic mode, but Laya is off, so every comment follows "
                        f"the fallback: {'published after basic checks' if s['laya_fallback'] == 'publish' else 'held for review'}.")
    body = render(
        """<h1>Settings</h1>
{warnings}
{errors}
<form method="post" class="stack settings">{csrf}
<section class="panel"><h2>Your blog</h2>
{origins}{open}
</section>
<section class="panel"><h2>Moderation</h2>
{mode}
<div class="sub">{decider}
<p class="help">Laya’s thresholds and what happens when it cannot answer are on the <a href="{laya}">Laya page</a>.</p></div>
</section>
<section class="panel"><h2>The comment form</h2>
{formatting}{linkify}{email}{pseudonyms}
<p class="help sub">Readers never need an account, an email address or another site’s login for this. <a href="{pseudonyms_page}">About pseudonyms, and the names held so far</a>.</p>
{order}{maxbody}
</section>
<section class="panel"><h2>Spam protection</h2>
<p class="help">These checks run before any moderation mode. Comments that fail the first three are refused with a message to the reader; the others hold a comment for review in automatic mode and are shown as notes in the queue.</p>
<div class="grid">{minsec}{rateip}{rateall}</div>
<div class="grid">{maxlinks}</div>
{dupes}{blocked}
</section>
<section class="panel"><h2>Housekeeping</h2>
{purge}
<p class="help">Email addresses are only visible here. The keyed network identifier used for rate limits is cleared from comments after 30 days.</p>
</section>
<p><button type="submit" class="primary">Save settings</button></p>
</form>""",
        warnings=join(render('<p class="flash flash-info">{w}</p>', w=w) for w in warnings),
        errors=Markup('<p class="flash flash-error" role="alert">Some settings need attention; '
                      'see the notes below.</p>') if errors else "",
        csrf=csrf_field(session), laya=req.url("/admin/laya"),
        pseudonyms_page=req.url("/admin/pseudonyms"),
        pseudonyms=form.checkbox("pseudonyms", "Let readers keep a name as a verified pseudonym",
                                 "A reader can tick a box to keep their name. Their browser holds a secret "
                                 "key, and only comments sent with it show the name with “✓ verified "
                                 "pseudonym”. Nobody else can post under that name or a look-alike; all "
                                 "other names are marked “unverified”. Older comments are never marked."),
        origins=form.textarea("site_origins", "Blog address",
                              "The address of the blog that shows the comments, for example "
                              "https://blog.example.com. One per line if you have several. Only these "
                              "sites can load and send comments.", rows=2,
                              placeholder="https://blog.example.com"),
        open=form.checkbox("comments_open", "Accept new comments",
                           "Turn off to close comments everywhere. Existing comments stay visible."),
        mode=form.radios("moderation_mode", "How new comments are handled", [
            ("manual", "Manual", "Every comment waits until you publish it."),
            ("assisted", "Assisted", "Every comment waits for you. Laya, if turned on, flags likely spam "
                                     "and lets you sort the queue by it."),
            ("automatic", "Automatic", "Comments can appear without waiting for you."),
        ]),
        decider=form.radios("automatic_decider", "In automatic mode, publish when", [
            ("basic", "The basic checks pass", "No Laya needed. Comments with too many links, blocked "
                                               "terms or duplicate text wait for you."),
            ("laya", "Laya considers it genuine", "Laya publishes, holds or rejects each comment using "
                                                  "your thresholds. Basic-check flags still hold a comment."),
        ]),
        formatting=form.radios("formatting", "Formatting readers can use", [
            ("plain", "Plain text", "Paragraphs and line breaks only."),
            ("basic", "Basic", "Also *emphasis*, `code` and links. No HTML, ever."),
        ]),
        linkify=form.checkbox("linkify", "Turn web addresses into links (Basic formatting only)",
                              "Links get rel=\"nofollow ugc\", so they do not pass search ranking."),
        email=form.checkbox("ask_email", "Ask for an optional email address",
                            "Shown only to you in this dashboard, never on the blog."),
        order=form.radios("thread_order", "Order on the page", [
            ("oldest", "Oldest first", "Reads like a conversation."),
            ("newest", "Newest first", ""),
        ]),
        maxbody=form.number("max_body_chars", "Longest comment", unit="characters"),
        minsec=form.number("min_seconds", "Minimum time before sending", unit="seconds",
                           help_text="Bots post instantly; people take longer."),
        rateip=form.number("rate_per_ip", "Comments per sender", unit="per 10 minutes"),
        rateall=form.number("rate_global", "Comments in total", unit="per hour",
                            help_text="A ceiling for sudden floods from many addresses."),
        maxlinks=form.number("max_links", "Links before a comment is held", unit="links"),
        dupes=form.checkbox("hold_duplicates", "Hold comments that repeat text posted in the last 7 days"),
        blocked=form.textarea("blocked_terms", "Blocked terms",
                              "One word or phrase per line. A comment whose name, email or text contains "
                              "one is held for review. Matching ignores case.", rows=4),
        purge=form.number("purge_rejected_days", "Delete rejected comments after", unit="days",
                          help_text="0 keeps them until you delete them."),
    )
    return page(app, req, "Settings", body, session=session, current="settings")


# ---------------------------------------------------------------------------
# pseudonyms
# ---------------------------------------------------------------------------

def pseudonyms_page(app, req: Request, session) -> Response:
    conn = app.db.conn()
    if req.method == "POST":
        raw = req.field("release")
        held = conn.execute("SELECT id, name FROM pseudonyms WHERE id = ?", (int(raw),)).fetchone() \
            if raw.isascii() and raw.isdigit() and len(raw) <= 12 else None
        if held is None:
            flash(app, session, "info", "That pseudonym had already been released.")
        else:
            with conn:
                conn.execute("DELETE FROM pseudonyms WHERE id = ?", (held["id"],))
            flash(app, session, "ok", f"Released “{held['name']}”. Its comments are no longer marked as "
                                      "verified, and anyone can now use or claim the name.")
        return redirect(req.url("/admin/pseudonyms"))
    rows = conn.execute(
        "SELECT p.id, p.name, p.created_at, SUM(c.status = 'approved') AS published, "
        "SUM(c.status = 'pending') AS pending, SUM(c.status = 'rejected') AS rejected, "
        "MAX(c.created_at) AS last FROM pseudonyms p LEFT JOIN comments c ON c.pseudonym_id = p.id "
        "GROUP BY p.id ORDER BY p.name COLLATE NOCASE, p.id LIMIT 1000").fetchall()
    now = time.time()
    table_rows = [render(
        """<tr><th scope="row"><a href="{list}">{name}</a></th><td>{since}</td><td>{published}</td>
<td>{pending}</td><td>{rejected}</td><td>{last}</td>
<td><form method="post">{csrf}<button type="submit" name="release" value="{id}" class="danger" data-confirm="{confirm}">Release</button></form></td></tr>""",
        list=_list_url(req, status="all", pseudonym=str(r["id"])), name=r["name"],
        since=ago(r["created_at"], now), published=r["published"] or 0, pending=r["pending"] or 0,
        rejected=r["rejected"] or 0, last=ago(r["last"], now), csrf=csrf_field(session), id=r["id"],
        confirm=f"Release “{r['name']}”? Its comments will no longer be marked as verified, and anyone "
                "can then use or claim the name.") for r in rows]
    table = render(
        """<table class="pseudonyms"><thead><tr><th>Name</th><th>Held since</th><th>Published</th>
<th>Pending</th><th>Rejected</th><th>Last comment</th><th><span class="visually-hidden">Actions</span></th></tr></thead>
<tbody>{rows}</tbody></table>""", rows=join(table_rows)) if table_rows else \
        Markup('<p class="muted">No reader has kept a pseudonym yet.</p>')
    off = Markup("") if app.settings.get("pseudonyms") else render(
        '<p class="flash flash-info">Pseudonyms are off. Readers cannot keep new ones, and while it stays '
        'off the names below are not reserved: anyone can post under them, without the mark. '
        '<a href="{h}">Turn them on in Settings</a>.</p>', h=req.url("/admin/settings"))
    body = render(
        """<h1>Pseudonyms</h1>
{off}
<section class="panel"><h2>How they work</h2>
<p>A reader who ticks “Keep this name as my pseudonym” gets a secret key in their browser. Afterword stores only a keyed hash of it, together with the name. Comments sent with the key show the name with “✓ verified pseudonym”. Nobody else can post under that name, or one that looks like it; every other name is shown as “unverified”. Readers can save the key and restore it on another device. Comments written before a name was claimed are never marked.</p>
<p>“Verified” means only that the comment was sent with the same key. Afterword does not know who holds it. A pseudonym links its comments to each other in public, and it hides nothing from this server: requests are handled exactly like any other comment.</p>
<p>A name stays held while at least one of its comments is kept here, rejected ones included, until they are deleted. Release a name that spam has taken, or whose holder lost their key and asked you to. Its comments are then shown as unverified and anyone can claim the name. You cannot tell a holder who lost their key from someone pretending to be them.</p>
</section>
<section class="panel"><h2>Names held <span class="count">{n}</span></h2>
{table}
</section>""", off=off, n=len(rows), table=table)
    return page(app, req, "Pseudonyms", body, session=session, current="pseudonyms")


# ---------------------------------------------------------------------------
# Laya
# ---------------------------------------------------------------------------

LAYA_KEYS = ["laya_enabled", "laya_source", "laya_url", "laya_api_key", "laya_model", "laya_timeout",
             "laya_flag_at", "laya_approve_below", "laya_reject_at", "laya_fallback",
             "laya_question", "laya_genuine", "laya_spam"]

STATE_TEXT = {
    "absent": "Not installed",
    "installing": "Installing…",
    "installed": "Installed, not running",
    "starting": "Starting…",
    "running": "Running",
    "failed": "Needs attention",
}


def _agreement(app, s: dict) -> Markup:
    rows = app.db.conn().execute(
        "SELECT status, laya_score FROM comments WHERE decided_by = 'admin' AND laya_status = 'scored' "
        "AND status IN ('approved', 'rejected') AND laya_score IS NOT NULL").fetchall()
    approved = [r["laya_score"] for r in rows if r["status"] == "approved"]
    rejected = [r["laya_score"] for r in rows if r["status"] == "rejected"]
    if not approved and not rejected:
        return Markup('<p class="help">Once you have published or rejected some comments that Laya '
                      'scored, this shows how often it agreed with you at the thresholds above. Use it to '
                      'tune them: Laya’s own documentation says its scores are not calibrated for any '
                      'particular site out of the box.</p>')

    def n(values, test):
        return sum(1 for v in values if test(v))

    flag, rej, pub = s["laya_flag_at"], s["laya_reject_at"], s["laya_approve_below"]
    return render(
        """<table class="agreement">
<thead><tr><th scope="col">You</th><th scope="col">Comments</th><th scope="col">Laya would publish</th>
<th scope="col">Laya would flag</th><th scope="col">Laya would reject</th></tr></thead>
<tbody>
<tr><th scope="row">Published</th><td>{a}</td><td>{a_pub}</td><td>{a_flag}</td><td class="{a_warn}">{a_rej}</td></tr>
<tr><th scope="row">Rejected</th><td>{r}</td><td class="{r_warn}">{r_pub}</td><td>{r_flag}</td><td>{r_rej}</td></tr>
</tbody></table>
<p class="help">“Would flag” uses the assisted-mode threshold; “would publish” and “would reject” use the automatic-mode thresholds. Highlighted cells are disagreements that matter most: genuine comments Laya would reject, and rejected ones it would publish.</p>""",
        a=len(approved), a_pub=n(approved, lambda v: v < pub), a_flag=n(approved, lambda v: v >= flag),
        a_rej=n(approved, lambda v: v >= rej), a_warn="warn" if n(approved, lambda v: v >= rej) else "",
        r=len(rejected), r_pub=n(rejected, lambda v: v < pub), r_flag=n(rejected, lambda v: v >= flag),
        r_rej=n(rejected, lambda v: v >= rej), r_warn="warn" if n(rejected, lambda v: v < pub) else "")


def _sentence(text: str) -> str:
    return (text[:1].upper() + text[1:] + ".") if text else ""


def laya_service(app, req: Request, session) -> Response:
    if req.method != "POST":
        return redirect(req.url("/admin/laya"))
    action = req.field("service")
    manager = app.laya_manager
    error = None
    if action == "install":
        device = "auto" if req.field("device") == "auto" else "cpu"
        error = manager.install(device)
        if not error:
            flash(app, session, "ok", "Installing Laya. This page refreshes until it is done.")
    elif action == "start":
        error = manager.start()
    elif action == "stop":
        manager.stop()
        flash(app, session, "ok", "Laya stopped.")
    elif action == "restart":
        error = manager.restart()
        app.laya.reset()
    elif action == "uninstall":
        manager.uninstall()
        if app.settings.get("laya_source") == "managed":
            app.settings.update({"laya_enabled": False})
        flash(app, session, "ok", "Laya was removed from this server and turned off.")
    if error:
        flash(app, session, "error", error)
    return redirect(req.url("/admin/laya"))


def laya_page(app, req: Request, session, test: bool = False) -> Response:
    errors: dict = {}
    values = app.settings.all()
    test_result = None
    if req.method == "POST" and not test:
        submitted = read_form(req, LAYA_KEYS)
        errors = app.settings.update(submitted)
        if not errors:
            s = app.settings.all()
            if s["laya_enabled"] and s["laya_source"] == "managed" and app.laya_manager.installed() \
                    and app.laya_manager.state in ("installed", "failed"):
                app.laya_manager.start()
            app.laya.reset()
            base_url, _, why_not = app._laya_endpoint(s)
            if s["laya_enabled"] and base_url is None and not (
                    s["laya_source"] == "managed" and app.laya_manager.state == "starting"):
                flash(app, session, "info", f"Saved. Laya is on, but {why_not}; until that is fixed, "
                                            "new comments follow the fallback and nothing is lost.")
            else:
                flash(app, session, "ok", "Laya settings saved.")
            return redirect(req.url("/admin/laya"))
        values = {**values, **submitted}
    s = app.settings.all()
    if test and req.method == "POST":
        from . import text as textmod
        name = textmod.clean(req.field("test_author")[:80], multiline=False) or "Reader"
        comment = textmod.clean(req.field("test_body")[:5000], multiline=True)
        if comment:
            test_result = app.laya.score(s, name, comment, bypass_cooldown=True)
    form = Form(values, errors)
    manager = app.laya_manager
    st = manager.status()
    client = app.laya.status()
    health = app.laya.health(s) if (s["laya_source"] == "external" or st["state"] == "running") else None

    # --- status panel
    if s["laya_source"] == "managed":
        state_line = render('<p class="state state-{k}"><strong>{t}</strong> {m}</p>',
                            k=st["state"], t=STATE_TEXT.get(st["state"], st["state"]), m=st["message"])
        info = st["info"]
        facts = []
        if info.get("version"):
            facts.append(f"Laya {info['version']}")
        if info.get("device"):
            facts.append("CPU build" if info["device"] == "cpu" else "GPU if available")
        if st["port"] and st["state"] == "running":
            facts.append(f"listening on 127.0.0.1:{st['port']}")
    else:
        state_line = render('<p class="state"><strong>Using a Laya server at</strong> <code>{u}</code></p>',
                            u=s["laya_url"] or "(no address set)")
        facts = []
    if health is not None:
        facts.append(("health check passed" + (f", loaded: {', '.join(health['loaded'])}"
                                                if health.get("loaded") else ""))
                     if health["ok"] else f"health check failed: {health['error']}")
    if client["last_latency_ms"] is not None:
        facts.append(f"last answer took {client['last_latency_ms']} ms")
    if client["cooling_down"]:
        facts.append("paused for 30 s after an error; comments follow the fallback meanwhile")
    last_error = render('<p class="help">Last error {when}: {e}</p>', when=ago(int(client["last_error_at"])),
                        e=client["last_error"]) if client["last_error"] else ""
    enabled_line = Markup('<p class="on">Laya is <strong>on</strong> for moderation.</p>') if s["laya_enabled"] \
        else Markup('<p class="off">Laya is <strong>off</strong>. Comments are not sent to it.</p>')

    # --- service controls
    controls = Markup("")
    if s["laya_source"] == "managed":
        buttons = []
        if st["state"] == "absent" or (st["state"] == "failed" and not st["installed"]):
            if st["can_install"]:
                mem = manager.memory_gb()
                buttons.append(render(
                    """<form method="post" action="{act}" class="install">{csrf}
<p>Installing downloads PyTorch and Laya ({pkg}) into <code>data/laya</code>, then the model from Hugging Face on first start. Plan for about <strong>4 GB of disk</strong> and <strong>2 GB of free memory</strong> while it runs{mem}. Nothing else on the server is changed, and <em>Remove</em> deletes all of it.</p>
<div class="field radios compact"><span class="legend">Build</span>
<div class="radio"><input type="radio" id="dev-cpu" name="device" value="cpu" checked><label for="dev-cpu"><strong>CPU</strong><span class="help">Smaller download. Right for most servers.</span></label></div>
<div class="radio"><input type="radio" id="dev-auto" name="device" value="auto"><label for="dev-auto"><strong>Use a GPU if present</strong><span class="help">Much larger download on Linux.</span></label></div>
</div>
<button type="submit" name="service" value="install" class="primary">Install Laya on this server</button>
</form>""", act=req.url("/admin/laya/service"), csrf=csrf_field(session), pkg=st["package"],
                    mem=f" (this server has {mem:.1f} GB)" if mem else ""))
            else:
                buttons.append(Markup('<p class="help">Installing from the dashboard is turned off on this '
                                      'server. Choose “Another server” below and enter its address.</p>'))
        elif st["installed"] and st["state"] not in ("installing",):
            acts = []
            if st["state"] in ("installed", "failed"):
                acts.append(("start", "Start Laya", "primary", ""))
            else:
                acts.append(("restart", "Restart", "", ""))
                acts.append(("stop", "Stop", "", ""))
            acts.append(("uninstall", "Remove Laya from this server", "danger",
                         "Remove Laya, its model and its Python environment from this server?"))
            buttons.append(render('<form method="post" action="{act}" class="row">{csrf}{b}</form>',
                                  act=req.url("/admin/laya/service"), csrf=csrf_field(session),
                                  b=join(render('<button type="submit" name="service" value="{v}" class="{c}"'
                                                '{confirm}>{l}</button>', v=v, l=l, c=c,
                                                confirm=render(' data-confirm="{m}"', m=m) if m else "")
                                         for v, l, c, m in acts)))
        controls = join(buttons)

    logs = []
    install_tail = manager.install_log_tail()
    serve_tail = manager.serve_log_tail()
    if install_tail:
        logs.append(render('<details{o}><summary>Install log</summary><pre class="log">{t}</pre></details>',
                           t=install_tail, o=Markup(" open") if st["state"] in ("installing", "failed") else ""))
    if serve_tail:
        logs.append(render('<details{o}><summary>Service log</summary><pre class="log">{t}</pre></details>',
                           t=serve_tail, o=Markup(" open") if st["state"] == "failed" else ""))

    test_html = Markup("")
    if test_result is not None:
        if test_result.ok:
            sc = test_result.score
            verdict = ("would be rejected" if sc >= s["laya_reject_at"] else
                       "would be published" if sc < s["laya_approve_below"] else "would be held")
            test_html = render('<p class="flash flash-ok" role="status">Spam score <strong>{v}</strong> '
                               '({ms} ms). In automatic mode with Laya deciding, this comment {verdict}; '
                               'in assisted mode it {flag}.</p>',
                               v=f"{sc:.2f}", ms=test_result.latency_ms, verdict=verdict,
                               flag="would be flagged as likely spam" if sc >= s["laya_flag_at"]
                               else "would not be flagged")
        else:
            test_html = render('<p class="flash flash-error" role="alert">Laya did not answer: {e}</p>',
                               e=test_result.error)

    refresh = 5 if st["state"] in ("installing", "starting") and s["laya_source"] == "managed" else None
    body = render(
        """<h1>Laya</h1>
<p class="lede">Laya is an optional, open-weights model that scores how likely a comment is spam. It runs on your own hardware; only the commenter’s name and comment text are sent to it, never email addresses or network details. Afterword works fully without it.</p>
<section class="panel status-panel"><h2>Status</h2>
{state_line}{enabled_line}
{facts}{last_error}
{controls}
{logs}
</section>
{errors}
<form method="post" class="stack">{csrf}
<section class="panel"><h2>Where Laya runs</h2>
{source}
<div class="sub">{url}{key}</div>
</section>
<section class="panel"><h2>Use Laya</h2>
{enabled}
{model}
<div class="grid">{flag_at}{approve}{reject}</div>
{fallback}
{timeout}
<details class="advanced"><summary>The question Laya is asked</summary>
<p class="help">Laya answers one two-option question about each comment. Changing the wording changes its scores, so test on real comments afterwards. The state it sees is <code>author</code> and <code>comment</code>.</p>
{question}{genuine}{spam}
</details>
</section>
<p><button type="submit" class="primary">Save Laya settings</button></p>
</form>
<section class="panel"><h2>Try it</h2>
{test_html}
<form method="post" action="{test_url}" class="stack">{csrf}
<div class="field"><label for="test_author">Name</label><input id="test_author" name="test_author" value="{t_author}"></div>
<div class="field"><label for="test_body">Comment</label><textarea id="test_body" name="test_body" rows="4">{t_body}</textarea></div>
<button type="submit">Score this comment</button>
</form>
</section>
<section class="panel"><h2>How Laya compares with your decisions</h2>
{agreement}
</section>""",
        state_line=state_line, enabled_line=enabled_line,
        facts=render('<p class="facts">{f}</p>', f=_sentence("; ".join(facts))) if facts else "",
        last_error=last_error, controls=controls, logs=join(logs),
        errors=Markup('<p class="flash flash-error" role="alert">Some settings need attention; see the '
                      'notes below.</p>') if errors else "",
        csrf=csrf_field(session),
        source=form.radios("laya_source", "Laya runs", [
            ("managed", "On this server", "Afterword installs and looks after it (use the button above)."),
            ("external", "Another server", "A laya-serve instance you run yourself, for example on a "
                                           "machine with a GPU."),
        ]),
        url=form.text("laya_url", "Address of the other server", "Used only when Laya runs on another "
                      "server. Its /v1/systemone endpoint is called.", kind="url",
                      placeholder="http://127.0.0.1:8000"),
        key=form.text("laya_api_key", "API key", "The LAYA_API_KEY that server was started with. "
                      + ("A key is stored; leave empty to keep it." if s["laya_api_key"] else "Optional."),
                      kind="password", value=""),
        enabled=form.checkbox("laya_enabled", "Use Laya for moderation",
                              "Laya is asked about new comments in the assisted and automatic modes. "
                              "Manual mode never uses it."),
        model=form.radios("laya_model", "Language", [
            ("auto", "Detect automatically", "English comments use the English model; others load the "
                                             "multilingual model on first use (the first such comment may time out)."),
            ("english", "English", "Uses the least memory."),
            ("multilingual", "Many languages", "One model for 100+ languages; slightly weaker on English."),
        ]),
        flag_at=form.number("laya_flag_at", "Flag as likely spam at", "Assisted mode.", step="0.01"),
        approve=form.number("laya_approve_below", "Publish below", "Automatic mode with Laya deciding.",
                            step="0.01"),
        reject=form.number("laya_reject_at", "Reject at", "Automatic mode. Rejected comments stay in the "
                           "Rejected tab; nothing is lost.", step="0.01"),
        fallback=form.radios("laya_fallback", "If Laya cannot answer (automatic mode)", [
            ("hold", "Hold the comment for review", "Recommended. Nothing is published unchecked."),
            ("publish", "Publish it if the basic checks pass", "Keeps conversations flowing if Laya is down."),
        ]),
        timeout=form.number("laya_timeout", "Wait for an answer for up to", step="0.5", unit="seconds",
                            help_text="The reader waits this long at most. After an error Laya is left "
                                      "alone for 30 seconds."),
        question=form.text("laya_question", "Question"),
        genuine=form.text("laya_genuine", "Option A: what a genuine comment is"),
        spam=form.text("laya_spam", "Option B: what spam is"),
        test_url=req.url("/admin/laya/test"), test_html=test_html,
        t_author=req.field("test_author") if test else "",
        t_body=req.field("test_body") if test else "",
        agreement=_agreement(app, s))
    return page(app, req, "Laya", body, session=session, current="laya", refresh=refresh)


# ---------------------------------------------------------------------------
# embedding instructions, account, backup
# ---------------------------------------------------------------------------

def embed_page(app, req: Request, session) -> Response:
    base = (app.cfg.public_url or req.origin) + req.script_name
    widget = base + "/widget.js"
    sri = "sha384-" + base64.b64encode(hashlib.sha384(app.static["/widget.js"][0]).digest()).decode()
    s = app.settings.all()
    writefreely = (
        '{{if and .IsFound (not .IsPinned)}}\n'
        '<section id="comments" data-afterword data-thread="{{.ID}}"></section>\n'
        f'<script src="{widget}" defer></script>\n'
        '{{end}}'
    )
    generic = (
        '<div data-afterword></div>\n'
        f'<script src="{widget}" defer></script>'
    )
    pinned = f'<script src="{widget}" integrity="{sri}" crossorigin="anonymous" defer></script>'
    body = render(
        """<h1>Add comments to your blog</h1>
{origin_note}
<section class="panel"><h2>WriteFreely</h2>
<ol class="steps">
<li>Open <code>templates/collection-post.tmpl</code> in your WriteFreely folder.</li>
<li>Paste this directly after the line containing <code>&lt;/article&gt;</code>:
<pre class="snippet"><code>{wf}</code></pre></li>
<li>Restart WriteFreely. Its templates are read at start-up.</li>
</ol>
<p class="help"><code>{{{{.ID}}}}</code> is WriteFreely’s permanent post id, so comments stay attached if you change a post’s slug. Pinned posts (your static pages) get no comments. WriteFreely upgrades replace the template, so re-apply this afterwards; <code>contrib/writefreely/add-comments.py</code> does it for you.</p>
</section>
<section class="panel"><h2>Any other site</h2>
<pre class="snippet"><code>{generic}</code></pre>
<p class="help">Without <code>data-thread</code>, each page’s path identifies its thread. Use <code>data-thread-from="canonical"</code> to use the page’s canonical link instead.</p>
</section>
<section class="panel"><h2>Styling</h2>
<p>The widget adds plain elements with <code>afterword-…</code> classes to your page, so your blog’s stylesheet already applies to its headings, inputs and buttons. In WriteFreely, add rules under <em>Customize → Custom CSS</em>. Every class is listed in <code>docs/styling.md</code>. For a neutral starting point, add <code>&lt;link rel="stylesheet" href="{css}"&gt;</code>.</p>
</section>
<section class="panel"><h2>Optional: pin the widget</h2>
<p>If you want your blog to refuse a modified widget, load it with a Subresource Integrity hash. Update the hash whenever you upgrade Afterword, or the comments will stop loading.</p>
<pre class="snippet"><code>{pinned}</code></pre>
</section>""",
        origin_note=Markup("") if s["site_origins"] else render(
            '<p class="flash flash-info">First, add your blog’s address in <a href="{h}">Settings</a>. '
            'Until then browsers will refuse to load comments.</p>', h=req.url("/admin/settings")),
        wf=writefreely, generic=generic, pinned=pinned, css=base + "/afterword.css")
    return page(app, req, "Add to your blog", body, session=session, current="embed")


def account_page(app, req: Request, session) -> Response:
    error = ""
    if req.method == "POST":
        action = req.field("do")
        if action == "password":
            current, new, confirm = req.field("current"), req.field("password"), req.field("confirm")
            if not security.verify_password(current, app.db.meta_get("admin_password")):
                error = "Your current password is not right."
            elif security.password_problem(new):
                error = security.password_problem(new)
            elif new != confirm:
                error = "The two new passwords are different."
            else:
                app.db.meta_set("admin_password", security.hash_password(new))
                conn = app.db.conn()
                with conn:
                    conn.execute("DELETE FROM sessions WHERE token_hash != ?", (session["token_hash"],))
                flash(app, session, "ok", "Password changed. Other sessions were signed out.")
                return redirect(req.url("/admin/account"))
        elif action == "signout-all":
            conn = app.db.conn()
            with conn:
                conn.execute("DELETE FROM sessions")
            resp = redirect(req.url("/admin/login"))
            resp.set_cookie(cookie_name(app), "", max_age=0, secure=not app.cfg.dev)
            return resp
    stats = app.db.counters_since(7)
    stat_rows = [("Comments received", stats.get("received", 0)),
                 ("Published automatically", stats.get("published", 0)),
                 ("Held for review", stats.get("held", 0)),
                 ("Rejected by Laya", stats.get("rejected_auto", 0)),
                 ("Blocked: hidden field filled in", stats.get("honeypot", 0)),
                 ("Blocked: sent too quickly", stats.get("too_fast", 0)),
                 ("Blocked: rate limit", stats.get("rate_limited", 0)),
                 ("Blocked: sent from another site", stats.get("origin_blocked", 0)),
                 ("Laya errors", stats.get("laya_error", 0))]
    body = render(
        """<h1>Account</h1>
<section class="panel"><h2>Last 7 days</h2>
<table class="stats"><tbody>{stats}</tbody></table>
</section>
<section class="panel"><h2>Change password</h2>{error}
<form method="post" class="stack">{csrf}<input type="hidden" name="do" value="password">
<div class="field"><label for="current">Current password</label><input id="current" name="current" type="password" autocomplete="current-password" required></div>
<div class="field"><label for="password">New password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="{min}" required></div>
<div class="field"><label for="confirm">Repeat new password</label><input id="confirm" name="confirm" type="password" autocomplete="new-password" required></div>
<button type="submit">Change password</button>
</form></section>
<section class="panel"><h2>Backup</h2>
<p>Everything Afterword knows is in one SQLite file. Download a consistent copy now, or schedule <code>python -m afterword backup /path/to/afterword-$(date +%F).db</code>.</p>
<p><a class="button" href="{backup}">Download backup</a></p>
<p class="help">The backup contains email addresses. Store it like any other private data.</p>
</section>
<section class="panel"><h2>Sessions</h2>
<form method="post">{csrf}<input type="hidden" name="do" value="signout-all">
<button type="submit">Sign out everywhere</button></form>
</section>""",
        stats=join(render("<tr><th scope=\"row\">{l}</th><td>{n}</td></tr>", l=l, n=n) for l, n in stat_rows),
        error=render('<p class="flash flash-error" role="alert">{e}</p>', e=error) if error else "",
        csrf=csrf_field(session), min=security.MIN_PASSWORD_LENGTH, backup=req.url("/admin/backup"))
    return page(app, req, "Account", body, session=session, current="account")


def backup(app, req: Request, session) -> Response:
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db", dir=app.cfg.data_dir)
    os.close(fd)
    try:
        app.db.backup_to(path)
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        os.remove(path)
    name = time.strftime("afterword-%Y-%m-%d.db", time.gmtime())
    return Response(data, content_type="application/vnd.sqlite3",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
