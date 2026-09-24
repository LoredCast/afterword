"""Security primitives. Standard library only."""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time

# scrypt with N=2^15, r=8 uses 32 MiB per hash: slow enough to hurt offline
# guessing, cheap enough for a single admin logging in.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 12


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Burn comparable time so "no password set" is not observable.
        hash_password(password)
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=_b64d(salt), n=int(n), r=int(r), p=int(p),
            maxmem=_SCRYPT_MAXMEM, dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _b64d(digest))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 1024:
        return "Use at most 1024 characters."
    return None


def token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def public_id() -> str:
    """Short random id for comments (safe in URLs and HTML ids)."""
    return base64.b32encode(secrets.token_bytes(10)).decode("ascii").lower()


def setup_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


class FormTokens:
    """Signed, timestamped tokens handed out with each thread.

    They let the server reject submissions that arrive impossibly fast after the
    form was shown, or that never loaded the form at all. They are not secrets
    and not a CAPTCHA: a determined bot can fetch one first. Each token is
    accepted once, so a single fetch cannot be replayed for a flood.
    """

    MAX_AGE = 2 * 86400

    def __init__(self, secret: bytes):
        self._secret = secret
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()

    def _sig(self, thread: str, issued: int, nonce: str) -> str:
        msg = f"{thread}\x00{issued}\x00{nonce}".encode("utf-8")
        return _b64e(hmac.new(self._secret, msg, hashlib.sha256).digest()[:18])

    def issue(self, thread: str, now: float | None = None) -> str:
        issued = int(now if now is not None else time.time())
        nonce = secrets.token_urlsafe(9)
        return f"{issued}.{nonce}.{self._sig(thread, issued, nonce)}"

    def check(self, value: str, thread: str, min_age: float, now: float | None = None) -> str | None:
        """Return None if valid, else an error code."""
        now = now if now is not None else time.time()
        try:
            issued_s, nonce, sig = str(value).split(".")
            issued = int(issued_s)
        except (ValueError, AttributeError):
            return "form_expired"
        if not hmac.compare_digest(sig, self._sig(thread, issued, nonce)):
            return "form_expired"
        age = now - issued
        if age > self.MAX_AGE or age < -60:
            return "form_expired"
        if age < min_age:
            return "too_fast"
        with self._lock:
            if nonce in self._used:
                return "form_expired"
        return None

    def consume(self, value: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        try:
            nonce = str(value).split(".")[1]
        except IndexError:
            return
        with self._lock:
            self._used[nonce] = now
            if len(self._used) > 200_000:
                self.prune(now)

    def prune(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            cutoff = now - self.MAX_AGE - 120
            self._used = {n: t for n, t in self._used.items() if t > cutoff}


def ip_key(secret: bytes, address: str) -> str:
    """Keyed hash of the sender's network, used for rate limits.

    IPv6 is grouped by /64 because a single household usually gets a whole
    /64. The raw address is never stored.
    """
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.version == 6:
            ip = ipaddress.ip_network(f"{ip}/64", strict=False).network_address
        material = str(ip)
    except ValueError:
        material = "unknown"
    return hmac.new(secret, material.encode("ascii"), hashlib.sha256).hexdigest()[:16]


class RateLimiter:
    """Sliding-window counter kept in memory (restarts reset it, by design)."""

    def __init__(self, max_keys: int = 50_000):
        self._hits: dict[str, collections.deque] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def hit(self, key: str, limit: int, window: float, now: float | None = None,
            record: bool = True) -> float:
        """Return 0 if allowed (and record it), else seconds until retry."""
        now = now if now is not None else time.time()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    self._prune_locked(now, window)
                hits = self._hits[key] = collections.deque()
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1.0, hits[0] + window - now)
            if record:
                hits.append(now)
            return 0.0

    def count(self, key: str, window: float, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return sum(1 for t in hits if t > now - window)

    def _prune_locked(self, now: float, window: float) -> None:
        stale = [k for k, d in self._hits.items() if not d or d[-1] <= now - max(window, 86400)]
        for key in stale:
            del self._hits[key]
        if len(self._hits) >= self._max_keys:
            # Still full: drop the oldest half rather than grow without bound.
            ordered = sorted(self._hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0)
            for key, _ in ordered[: len(ordered) // 2]:
                del self._hits[key]

    def prune(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self._prune_locked(now, 86400)
