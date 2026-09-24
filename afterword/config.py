"""Bootstrap configuration.

Only things needed *before* the database is open live here, and they come from
environment variables so the same code runs under systemd, Docker or a shell.
Everything an owner is expected to change day to day lives in the database and
is edited from the dashboard (see settings.py).
"""
from __future__ import annotations

import dataclasses
import os
from urllib.parse import urlsplit

DEFAULT_LAYA_PACKAGE = "laya[serve]==0.3.10"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclasses.dataclass
class Config:
    data_dir: str = "./data"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    public_url: str | None = None      # e.g. https://comments.example.com
    trusted_proxy: str | None = "127.0.0.1"
    url_prefix: str = ""               # e.g. /comments when served under a sub-path
    dev: bool = False                  # allow non-Secure cookies on plain http
    allow_laya_install: bool = True
    laya_package: str = DEFAULT_LAYA_PACKAGE
    threads: int = 8

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "afterword.db")

    @property
    def laya_dir(self) -> str:
        return os.path.join(self.data_dir, "laya")

    @property
    def public_origin(self) -> str | None:
        if not self.public_url:
            return None
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}".lower()

    def validate(self) -> list[str]:
        problems = []
        if self.public_url:
            parts = urlsplit(self.public_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                problems.append("AFTERWORD_PUBLIC_URL must look like https://comments.example.com")
        if self.url_prefix and not self.url_prefix.startswith("/"):
            problems.append("AFTERWORD_URL_PREFIX must start with '/'")
        if not (0 < self.listen_port < 65536):
            problems.append("listen port out of range")
        return problems


def from_env(env: dict | None = None, **overrides) -> Config:
    env = os.environ if env is None else env
    listen = env.get("AFTERWORD_LISTEN", "127.0.0.1:8080")
    host, _, port = listen.rpartition(":")
    host = host.strip("[]") or "127.0.0.1"
    proxy = env.get("AFTERWORD_TRUSTED_PROXY", "127.0.0.1").strip() or None
    cfg = Config(
        data_dir=env.get("AFTERWORD_DATA", "./data"),
        listen_host=host,
        listen_port=int(port or 8080),
        public_url=(env.get("AFTERWORD_PUBLIC_URL") or "").rstrip("/") or None,
        trusted_proxy=proxy,
        url_prefix=(env.get("AFTERWORD_URL_PREFIX") or "").rstrip("/"),
        dev=_bool(env.get("AFTERWORD_DEV"), False),
        allow_laya_install=_bool(env.get("AFTERWORD_LAYA_INSTALL"), True),
        laya_package=env.get("AFTERWORD_LAYA_PACKAGE") or DEFAULT_LAYA_PACKAGE,
        threads=int(env.get("AFTERWORD_THREADS", "8")),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg
