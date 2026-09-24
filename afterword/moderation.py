"""Moderation policy.

``decide()`` is a pure function from (settings, basic-check flags, Laya result)
to a status plus a sentence explaining it. The sentence is stored with the
comment and shown on the dashboard, so every automatic decision can be read
back later in plain language.

Mode         What happens to a new comment
-----------  ------------------------------------------------------------------
manual       Always pending. Laya is never consulted.
assisted     Always pending. If Laya is on, it scores the comment so the
             dashboard can flag and sort likely spam. The owner decides.
automatic    basic: published if it passes the basic checks, else pending.
             laya:  Laya decides with the owner's thresholds; basic-check
                    flags still hold a comment. If Laya cannot answer, the
                    fallback policy applies (hold by default).

Rejected comments are kept in the Rejected folder (never silently dropped) until
the owner deletes them or the retention period ends.
"""
from __future__ import annotations

import dataclasses
import time

from . import text as textmod
from .laya import LayaResult


@dataclasses.dataclass
class Decision:
    status: str                  # pending | approved | rejected
    decided_by: str | None       # None (waiting), 'auto', 'laya'
    reason: str


def basic_flags(settings: dict, conn, author: str, body: str, email: str | None,
                now: float | None = None) -> list[str]:
    """Soft checks: a flag holds a comment in automatic mode; it never rejects."""
    now = now or time.time()
    flags = []
    links = textmod.count_links(body)
    if links > settings["max_links"]:
        flags.append(f"links:{links}")
    terms = [t.strip().casefold() for t in settings["blocked_terms"].splitlines() if t.strip()]
    if terms:
        haystack = "\n".join((author, body, email or "")).casefold()
        hit = next((t for t in terms if t in haystack), None)
        if hit:
            flags.append(f"blocked:{hit[:60]}")
    if settings["hold_duplicates"]:
        digest = body_hash(body)
        row = conn.execute(
            "SELECT 1 FROM comments WHERE body_hash = ? AND created_at > ? LIMIT 1",
            (digest, int(now) - 7 * 86400),
        ).fetchone()
        if row:
            flags.append("duplicate")
    return flags


def body_hash(body: str) -> str:
    from .security import sha256_hex
    return sha256_hex(textmod.fingerprint(body))


def describe_flag(flag: str, settings: dict | None = None) -> str:
    kind, _, value = flag.partition(":")
    if kind == "links":
        limit = f" (limit {settings['max_links']})" if settings else ""
        return f"{value} links{limit}"
    if kind == "blocked":
        return f"contains blocked term “{value}”"
    if kind == "duplicate":
        return "same text was posted in the last 7 days"
    return flag


def wants_laya(settings: dict) -> bool:
    mode = settings["moderation_mode"]
    if mode == "manual":
        return False
    if mode == "automatic" and settings["automatic_decider"] == "laya":
        return True
    return bool(settings["laya_enabled"])


def decide(settings: dict, flags: list[str], laya: LayaResult | None) -> Decision:
    mode = settings["moderation_mode"]
    held_for = "; ".join(describe_flag(f, settings) for f in flags)

    if mode == "manual":
        return Decision("pending", None, "Manual moderation: waiting for your review.")

    if mode == "assisted":
        if laya is not None and laya.ok:
            note = "likely spam" if laya.score >= settings["laya_flag_at"] else "not flagged"
            return Decision("pending", None,
                            f"Assisted moderation: Laya spam score {laya.score:.2f} ({note}).")
        if laya is not None:
            return Decision("pending", None, f"Assisted moderation: not scored ({laya.error}).")
        return Decision("pending", None, "Assisted moderation: waiting for your review.")

    # automatic
    if settings["automatic_decider"] == "basic":
        if flags:
            return Decision("pending", None, f"Held by basic checks: {held_for}.")
        return Decision("approved", "auto", "Published automatically: passed the basic checks.")

    if laya is None or not laya.ok:
        error = laya.error if laya is not None else "Laya is turned off"
        if not settings["laya_enabled"]:
            error = "Laya is turned off"
        if settings["laya_fallback"] == "publish" and not flags:
            return Decision("approved", "auto",
                            f"Laya unavailable ({error}); fallback published it after the basic checks.")
        extra = f" Basic checks also flagged: {held_for}." if flags else ""
        return Decision("pending", None, f"Laya unavailable ({error}); held for review.{extra}")

    score = laya.score
    if score >= settings["laya_reject_at"]:
        return Decision("rejected", "laya",
                        f"Rejected by Laya: spam score {score:.2f} ≥ {settings['laya_reject_at']:.2f}.")
    if flags:
        return Decision("pending", None,
                        f"Held by basic checks: {held_for}. Laya spam score {score:.2f}.")
    if score < settings["laya_approve_below"]:
        return Decision("approved", "laya",
                        f"Published by Laya: spam score {score:.2f} < {settings['laya_approve_below']:.2f}.")
    return Decision("pending", None,
                    f"Held: Laya was unsure (spam score {score:.2f}, between "
                    f"{settings['laya_approve_below']:.2f} and {settings['laya_reject_at']:.2f}).")
