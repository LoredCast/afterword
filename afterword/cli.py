"""Command line: ``python -m afterword [serve|set-password|backup FILE|setup-code|version]``."""
from __future__ import annotations

import argparse
import atexit
import getpass
import logging
import os
import signal
import sys

from . import __version__, security
from .config import from_env

log = logging.getLogger("afterword")


def _app(args):
    from .app import App
    cfg = from_env()
    if getattr(args, "data", None):
        cfg.data_dir = args.data
    if getattr(args, "listen", None):
        host, _, port = args.listen.rpartition(":")
        cfg.listen_host, cfg.listen_port = host.strip("[]") or "127.0.0.1", int(port)
    problems = cfg.validate()
    if problems:
        sys.exit("Configuration problem: " + "; ".join(problems))
    return App(cfg)


def cmd_serve(args) -> None:
    app = _app(args)
    cfg = app.cfg
    base = cfg.public_url or f"http://{cfg.listen_host}:{cfg.listen_port}{cfg.url_prefix}"
    code = app.new_setup_code()
    if code:
        log.warning("No dashboard password is set yet. Open %s/admin/setup?code=%s "
                    "(setup code %s, valid 24 hours) to choose one.", base, code, code)
    app.start_background()
    atexit.register(app.stop)
    # Make "systemctl stop" and "docker stop" run the atexit handlers, which
    # also stop a managed Laya service.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log.info("Afterword %s listening on %s:%s (data in %s)", __version__, cfg.listen_host,
             cfg.listen_port, os.path.abspath(cfg.data_dir))
    try:
        import waitress
    except ImportError:
        log.warning("waitress is not installed; using Python's development server. "
                    "Install waitress for production use.")
        from wsgiref.simple_server import make_server
        make_server(cfg.listen_host, cfg.listen_port, app).serve_forever()
        return
    options = dict(host=cfg.listen_host, port=cfg.listen_port, threads=cfg.threads,
                   url_prefix=cfg.url_prefix, max_request_body_size=1024 * 1024, ident="afterword",
                   clear_untrusted_proxy_headers=True)
    if cfg.trusted_proxy:
        options.update(trusted_proxy=cfg.trusted_proxy, trusted_proxy_count=1,
                       trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"})
    waitress.serve(app, **options)


def cmd_set_password(args) -> None:
    app = _app(args)
    if args.stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("New dashboard password: ")
        if getpass.getpass("Repeat it: ") != password:
            sys.exit("The two passwords are different.")
    problem = security.password_problem(password)
    if problem:
        sys.exit(problem)
    app.db.meta_set("admin_password", security.hash_password(password))
    conn = app.db.conn()
    with conn:
        conn.execute("DELETE FROM sessions")
    print("Password saved. All dashboard sessions were signed out.")


def cmd_backup(args) -> None:
    app = _app(args)
    if os.path.exists(args.file):
        sys.exit(f"{args.file} already exists; choose a new file name.")
    app.db.backup_to(args.file)
    print(f"Backed up to {args.file}")


def cmd_setup_code(args) -> None:
    app = _app(args)
    code = app.new_setup_code()
    if code is None:
        sys.exit("A password is already set. Use `python -m afterword set-password` to change it.")
    print(f"Setup code (valid 24 hours): {code}")


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="afterword", description="Afterword comment server")
    parser.add_argument("--data", help="data directory (default: $AFTERWORD_DATA or ./data)")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the server (default)")
    serve.add_argument("--listen", help="host:port (default: $AFTERWORD_LISTEN or 127.0.0.1:8080)")
    pw = sub.add_parser("set-password", help="set or reset the dashboard password")
    pw.add_argument("--stdin", action="store_true", help="read the password from standard input")
    bk = sub.add_parser("backup", help="write a consistent copy of the database")
    bk.add_argument("file")
    sub.add_parser("setup-code", help="print a new first-run setup code")
    sub.add_parser("version")
    args = parser.parse_args(argv)
    command = args.command or "serve"
    if command == "version":
        print(__version__)
    elif command == "serve":
        cmd_serve(args)
    elif command == "set-password":
        cmd_set_password(args)
    elif command == "backup":
        cmd_backup(args)
    elif command == "setup-code":
        cmd_setup_code(args)


if __name__ == "__main__":
    main()
