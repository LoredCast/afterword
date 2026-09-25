"""Pseudonyms: names a reader can keep without an account.

A reader who ticks "Keep this name as my pseudonym" gets a random key made in
their own browser. The first comment sent with that key claims the name; every
later comment sent with the same key is marked as coming from the same holder.
While pseudonyms are on, nobody else can post under that name (or one that
looks like it), with or without the mark.

What the server keeps per pseudonym: the name, a comparison form of it and a
keyed hash (HMAC-SHA-256 under the server's secret) of the key. Never the key
itself, never anything about the holder. A comment is marked as verified only
at the moment it is posted with the key, so older comments are never marked
after the fact, and releasing a pseudonym removes the mark from its comments.

A pseudonym is not anonymity: its comments are publicly linked to each other,
and the server sees each request's network address exactly as it does for any
other comment.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import re
import sqlite3
import unicodedata

# 32 characters of RFC 4648 base32 (a-z, 2-7): 160 random bits from the browser.
_KEY = re.compile(r"[a-z2-7]{32}")

# Readers could otherwise imitate the badge with a check mark in their name.
CHECK_MARKS = frozenset("✓✔☑✅√\U0001f5f8\U0001f5f9")

# Letters that look like other letters once case is folded. Mapping too much is
# harmless (a new name is refused as "too close"); mapping too little lets a
# look-alike through, which is why the badge, not the name, is what readers check.
_LOOKALIKE = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "і": "l", "ј": "j", "ӏ": "l",
    "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ζ": "z", "η": "h", "ι": "l", "κ": "k", "μ": "m",
    "ν": "n", "ο": "o", "ρ": "p", "τ": "t", "υ": "y", "χ": "x",
    # Latin letters without a decomposition, and digits that pass for letters.
    # "i" joins "l" because a capital I and a small l look the same in many fonts.
    "ı": "l", "i": "l", "1": "l", "ǀ": "l", "ł": "l", "ø": "o", "0": "o", "đ": "d", "ħ": "h",
})


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


@dataclasses.dataclass
class Claim:
    """A verified author for one comment: an existing pseudonym, or a new one to create."""
    id: int | None
    name: str
    name_key: str
    key_hash: str


def normalize_key(raw) -> str | None:
    if not isinstance(raw, str) or len(raw) > 100:
        return None
    key = re.sub(r"[\s-]", "", raw).lower()
    return key if _KEY.fullmatch(key) else None


def key_hash(secret: bytes, key: str) -> str:
    return hmac.new(secret, b"pseudonym-key\x00" + key.encode("ascii"), hashlib.sha256).hexdigest()


def name_key(name: str) -> str:
    """Comparison form: equal for names a reader could mistake for each other."""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = unicodedata.normalize("NFD", text)
    # Keep letters and digits only: drops accents, spaces, punctuation, symbols and emoji.
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] in "LN")
    text = text.translate(_LOOKALIKE)
    return text.replace("rn", "m").replace("vv", "w")


def has_check_mark(name: str) -> bool:
    return any(ch in CHECK_MARKS for ch in name)


def _taken(conn, key: str) -> bool:
    return conn.execute("SELECT 1 FROM pseudonyms WHERE name_key = ?", (key,)).fetchone() is not None


def _taken_problem() -> Problem:
    return Problem(409, "pseudonym_taken",
                   "That name, or one that looks very like it, is already someone’s pseudonym here. "
                   "Please choose another.", "author")


def resolve(conn, secret: bytes, author: str, raw_key) -> Claim | None:
    """Check a submission's name and key. Returns the verified author, or None if unverified.

    Read-only: a new pseudonym is only created by attach(), together with its comment.
    """
    if raw_key in (None, ""):
        wanted = name_key(author)
        if wanted and _taken(conn, wanted):
            raise Problem(409, "name_reserved",
                          f"“{author}” is too close to a pseudonym another reader holds here. Please "
                          "choose a different name, or restore your backup key if the pseudonym is yours.",
                          "author")
        return None
    key = normalize_key(raw_key)
    if key is None:
        raise Problem(400, "pseudonym_key_invalid", "That pseudonym key is not valid.")
    digest = key_hash(secret, key)
    row = conn.execute("SELECT id, name, name_key FROM pseudonyms WHERE key_hash = ?", (digest,)).fetchone()
    if row is not None:
        if row["name"] != author:
            raise Problem(400, "pseudonym_mismatch",
                          f"This key belongs to the pseudonym “{row['name']}”.", "author")
        return Claim(row["id"], row["name"], row["name_key"], digest)
    wanted = name_key(author)
    if not wanted:
        raise Problem(400, "pseudonym_name_invalid",
                      "A pseudonym needs at least one letter or digit.", "author")
    if _taken(conn, wanted):
        raise _taken_problem()
    return Claim(None, author, wanted, digest)


def attach(conn, claim: Claim | None, now: int) -> int | None:
    """Inside the comment's transaction: the pseudonym id to store with it, creating it if new."""
    if claim is None:
        return None
    if claim.id is not None:
        return claim.id
    try:
        return conn.execute(
            "INSERT INTO pseudonyms (name, name_key, key_hash, created_at) VALUES (?, ?, ?, ?)",
            (claim.name, claim.name_key, claim.key_hash, now)).lastrowid
    except sqlite3.IntegrityError:
        # Another request got there first: the same reader sending twice is fine.
        row = conn.execute("SELECT id, name FROM pseudonyms WHERE key_hash = ?",
                           (claim.key_hash,)).fetchone()
        if row is not None and row["name"] == claim.name:
            return row["id"]
        raise _taken_problem() from None


def lookup(conn, secret: bytes, raw_key):
    """The pseudonym a backup key belongs to, for restoring it on another device."""
    key = normalize_key(raw_key)
    if key is None:
        return None
    return conn.execute("SELECT id, name FROM pseudonyms WHERE key_hash = ?",
                        (key_hash(secret, key),)).fetchone()


def forget_orphans(conn) -> int:
    """Release pseudonyms with no comments left, so rejected spam cannot hold names forever."""
    return conn.execute("DELETE FROM pseudonyms WHERE NOT EXISTS "
                        "(SELECT 1 FROM comments WHERE comments.pseudonym_id = pseudonyms.id)").rowcount
