"""Cleaning reader input and turning it into a safe, structured body.

Comments are stored as plain text exactly as cleaned here. When they are shown,
the server never produces HTML: it returns a list of paragraphs, each a list of
typed segments, and the widget builds DOM nodes from those with textContent.
The complete formatting grammar is:

    plain   paragraphs (blank line) and line breaks
    basic   plain, plus *emphasis*, `code` and http(s) links (links optional)

Anything else, including HTML, Markdown headings, images or BBCode, is shown as
the literal text the reader typed.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

# Directional embeddings, overrides and isolates can make text display in a
# different order than it is stored ("Trojan Source"-style tricks).
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# Format characters worth keeping: joiners are needed for many emoji and scripts.
_KEEP_FORMAT = frozenset("\u200c\u200d")
# Characters that render as blank but are letters/symbols; used for "invisible" names.
_BLANK_LOOKALIKES = frozenset("\u115f\u1160\u3164\uffa0\u2800")
MAX_COMBINING_RUN = 8   # enough for real scripts, stops "Zalgo" text


def _is_tag_char(ch: str) -> bool:
    # Emoji tag sequences (e.g. subdivision flags) use U+E0020..U+E007F.
    return "\U000e0020" <= ch <= "\U000e007f"


def _is_noncharacter(ch: str) -> bool:
    cp = ord(ch)
    return 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE


def clean(value: str, *, multiline: bool) -> str:
    """Normalise untrusted text. Idempotent."""
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u2028", "\n").replace("\u2029", "\n\n")
    out: list[str] = []
    run = 0
    for ch in value:
        if ch == "\n":
            out.append("\n" if multiline else " ")
            run = 0
            continue
        category = unicodedata.category(ch)
        if category == "Cc":
            if ch == "\t":
                out.append(" ")
                run = 0
            continue
        if category == "Cs" or _is_noncharacter(ch) or ch in _BIDI_CONTROLS:
            continue
        if category == "Cf" and ch not in _KEEP_FORMAT and not _is_tag_char(ch):
            continue
        if not multiline and ch in _BLANK_LOOKALIKES:
            continue
        if category in ("Mn", "Me"):
            run += 1
            if run > MAX_COMBINING_RUN:
                continue
        else:
            run = 0
        out.append(ch)
    text = "".join(out)
    if not multiline:
        return re.sub(r"\s+", " ", text).strip()
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def has_visible_text(value: str) -> bool:
    return any(unicodedata.category(ch)[0] in "LNPS" for ch in value)


def fingerprint(value: str) -> str:
    """Text used to spot duplicate comments: case- and whitespace-insensitive."""
    return " ".join(value.casefold().split())


# -- links --------------------------------------------------------------------

_LINKISH = re.compile(r"(?i)https?://|www\.|\[url|<a\s")


def count_links(value: str) -> int:
    """How many link-like things a comment contains, rendered or not."""
    return len(_LINKISH.findall(value))


_TRAILING = ".,:;!?'\"*_"


def _trim_url(url: str) -> str:
    while url:
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        elif last == "]" and url.count("[") < url.count("]"):
            url = url[:-1]
        else:
            break
    return url


def safe_url(url: str) -> bool:
    """True only for plain http(s) URLs with an ASCII host and no credentials."""
    if not url or len(url) > 2000:
        return False
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError for a malformed port
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    if not parts.hostname or "@" in parts.netloc:
        return False
    if not parts.hostname.isascii():
        # Shown as text rather than linked: non-ASCII hosts can imitate other domains.
        return False
    return True


# -- rendering ------------------------------------------------------------------

_INLINE = re.compile(
    r"(?P<code>`[^`\n]{1,300}`)"
    r"|(?P<url>(?<![\w/@.])https?://[^\s<>\"'`]+)"
    r"|(?P<em>(?<![\w*\\])\*(?=[^\s*])(?:[^*\n]{0,299}[^\s*])?\*(?![\w*]))",
    re.IGNORECASE,
)


def _inline(line: str, linkify: bool) -> list[dict]:
    segments: list[dict] = []
    pos = 0

    def text(chunk: str) -> None:
        if chunk:
            segments.append({"t": "text", "v": chunk})

    for match in _INLINE.finditer(line):
        kind, token = match.lastgroup, match.group()
        if kind == "url":
            url = _trim_url(token)
            if not linkify or not safe_url(url):
                continue  # stays part of the surrounding text
            text(line[pos:match.start()])
            segments.append({"t": "a", "v": url})
            pos = match.start() + len(url)
            continue
        text(line[pos:match.start()])
        segments.append({"t": "code" if kind == "code" else "em", "v": token[1:-1]})
        pos = match.end()
    text(line[pos:])
    return segments


def _merge(segments: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for seg in segments:
        if seg["t"] == "text" and merged and merged[-1]["t"] == "text":
            merged[-1] = {"t": "text", "v": merged[-1]["v"] + seg["v"]}
        else:
            merged.append(seg)
    return merged


def render(value: str, *, formatting: str = "basic", linkify: bool = True) -> list[list[dict]]:
    """Return paragraphs of segments: {"t": "text"|"br"|"em"|"code"|"a", "v": str}."""
    paragraphs = []
    for block in value.split("\n\n"):
        if not block.strip():
            continue
        segments: list[dict] = []
        for index, line in enumerate(block.split("\n")):
            if index:
                segments.append({"t": "br"})
            if formatting == "basic":
                segments.extend(_inline(line, linkify))
            elif line:
                segments.append({"t": "text", "v": line})
        paragraphs.append(_merge(segments))
    return paragraphs
