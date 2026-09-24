"""SQLite storage.

One file, WAL mode, one connection per server thread. Back it up with
``python -m afterword backup FILE`` (uses SQLite's online backup API, safe while
the server is running) or the dashboard's download button.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

# Each entry is one schema version. Never edit an entry that has shipped;
# append a new one instead.
MIGRATIONS = [
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL            -- JSON
    );

    CREATE TABLE comments (
        id           INTEGER PRIMARY KEY,
        public_id    TEXT NOT NULL UNIQUE,
        thread       TEXT NOT NULL,
        page_url     TEXT,
        author       TEXT NOT NULL,
        email        TEXT,                              -- never exposed publicly
        body         TEXT NOT NULL,
        body_hash    TEXT NOT NULL,
        status       TEXT NOT NULL
                     CHECK (status IN ('pending', 'approved', 'rejected')),
        reason       TEXT NOT NULL DEFAULT '',          -- why it has this status
        decided_by   TEXT,                              -- NULL, 'auto', 'laya', 'admin'
        flags        TEXT NOT NULL DEFAULT '[]',        -- JSON list of basic-check flags
        created_at   INTEGER NOT NULL,
        decided_at   INTEGER,
        ip_key       TEXT,                              -- keyed hash, cleared after 30 days
        laya_status  TEXT,                              -- NULL, 'scored', 'error'
        laya_score   REAL,                              -- P(spam) from Laya
        laya_detail  TEXT                               -- JSON: model, latency, error
    );
    CREATE INDEX comments_thread ON comments (thread, status, created_at);
    CREATE INDEX comments_status ON comments (status, created_at);
    CREATE INDEX comments_hash ON comments (body_hash, created_at);

    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        csrf       TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    );

    CREATE TABLE counters (
        day  TEXT NOT NULL,
        name TEXT NOT NULL,
        n    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, name)
    );
    """,
]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self.migrate()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    # -- connections -----------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
        return conn

    def migrate(self) -> None:
        conn = self._open()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(MIGRATIONS, start=1):
                if index <= version:
                    continue
                conn.executescript(
                    "BEGIN;" + script + f";\nPRAGMA user_version = {index};\nCOMMIT;"
                )
        finally:
            conn.close()

    # -- meta ------------------------------------------------------------
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def meta_set(self, key: str, value: str | None) -> None:
        conn = self.conn()
        with conn:
            if value is None:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    # -- counters (small daily stats shown on the dashboard) --------------
    def bump(self, name: str, amount: int = 1) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        conn = self.conn()
        with conn:
            conn.execute(
                "INSERT INTO counters (day, name, n) VALUES (?, ?, ?) "
                "ON CONFLICT(day, name) DO UPDATE SET n = n + excluded.n",
                (day, name, amount),
            )

    def counters_since(self, days: int) -> dict[str, int]:
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        rows = self.conn().execute(
            "SELECT name, SUM(n) FROM counters WHERE day > ? GROUP BY name", (cutoff,)
        ).fetchall()
        return {name: total for name, total in rows}

    # -- backup ----------------------------------------------------------
    def backup_to(self, target_path: str) -> None:
        """Consistent online copy of the whole database."""
        src = self._open()
        try:
            dst = sqlite3.connect(target_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        try:
            os.chmod(target_path, 0o600)
        except OSError:
            pass
