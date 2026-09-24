"""Talking to a Laya decision service over its ``laya-serve`` HTTP protocol.

Laya is optional. Nothing here is imported from the ``laya`` package: the model
runs in its own process (managed by laya_manager.py, or anywhere the owner
points us), and we exchange JSON with ``POST /v1/systemone``.

Only the commenter's display name and comment text are sent. Email addresses
and network information never are.

Why a two-option ``choice`` question with keys ``A``/``B`` rather than a
``noul`` (yes/no) question: Laya's own documentation reports that ``noul``
answers can follow the literal true/false labels instead of the content on the
English checkpoint, and recommends neutral labels as a workaround.
"""
from __future__ import annotations

import dataclasses
import json
import math
import threading
import time
import urllib.error
import urllib.request

COOLDOWN_SECONDS = 30
MAX_STATE_CHARS = 2000   # the English checkpoint reads ~320 tokens of state anyway


@dataclasses.dataclass
class LayaResult:
    ok: bool
    score: float | None = None        # P(spam), 0..1
    model: str | None = None
    latency_ms: int | None = None
    error: str | None = None

    def detail_json(self) -> str:
        return json.dumps({k: v for k, v in dataclasses.asdict(self).items()
                           if k not in ("ok", "score") and v is not None})


def build_request(settings: dict, author: str, body: str) -> dict:
    request = {
        "state": {"author": author[:100], "comment": body[:MAX_STATE_CHARS]},
        "questions": {
            "spam": {
                "type": "choice",
                "instructions": settings["laya_question"],
                "criteria": {"A": settings["laya_genuine"], "B": settings["laya_spam"]},
            }
        },
    }
    if settings["laya_model"] in ("english", "multilingual"):
        request["model"] = settings["laya_model"]
    return request


def parse_response(data) -> tuple[float, str | None]:
    """Extract P(spam) or raise ValueError. Trusts nothing about the shape."""
    if not isinstance(data, dict):
        raise ValueError("response is not an object")
    answers = data.get("answers")
    if not isinstance(answers, dict) or not isinstance(answers.get("spam"), dict):
        raise ValueError("response has no answer for the spam question")
    probs = answers["spam"].get("probabilities")
    if not isinstance(probs, dict):
        raise ValueError("answer has no probabilities")
    spam, genuine = probs.get("B"), probs.get("A")
    for value in (spam, genuine):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("probabilities are not numbers")
    spam, genuine = float(spam), float(genuine)
    if not (0.0 <= spam <= 1.0 and 0.0 <= genuine <= 1.0):
        raise ValueError("probabilities out of range")
    total = spam + genuine
    score = spam / total if total > 0 else 0.5
    model = None
    routing = data.get("routing")
    if isinstance(routing, dict) and isinstance(routing.get("model"), str):
        model = routing["model"][:40]
    elif isinstance(data.get("model"), str):
        model = data["model"][:80]
    return score, model


class LayaClient:
    def __init__(self, endpoint_resolver):
        """endpoint_resolver(settings) -> (base_url | None, api_key | None, why_not)."""
        self._resolve = endpoint_resolver
        self._lock = threading.Lock()
        self.blocked_until = 0.0
        self.last_error: str | None = None
        self.last_error_at: float | None = None
        self.last_ok_at: float | None = None
        self.last_latency_ms: int | None = None

    def status(self) -> dict:
        with self._lock:
            return {
                "cooling_down": time.time() < self.blocked_until,
                "blocked_until": self.blocked_until,
                "last_error": self.last_error,
                "last_error_at": self.last_error_at,
                "last_ok_at": self.last_ok_at,
                "last_latency_ms": self.last_latency_ms,
            }

    def reset(self) -> None:
        with self._lock:
            self.blocked_until = 0.0

    def _fail(self, message: str) -> LayaResult:
        with self._lock:
            self.last_error = message
            self.last_error_at = time.time()
            self.blocked_until = time.time() + COOLDOWN_SECONDS
        return LayaResult(ok=False, error=message)

    def score(self, settings: dict, author: str, body: str, *, bypass_cooldown: bool = False) -> LayaResult:
        base_url, api_key, why_not = self._resolve(settings)
        if base_url is None:
            return LayaResult(ok=False, error=why_not or "Laya is not set up")
        if not bypass_cooldown and time.time() < self.blocked_until:
            wait = int(self.blocked_until - time.time()) + 1
            return LayaResult(ok=False, error=f"skipped: Laya failed recently, retrying in {wait}s")
        payload = json.dumps(build_request(settings, author, body)).encode("utf-8")
        request = urllib.request.Request(
            base_url + "/v1/systemone", data=payload, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if api_key:
            request.add_header("Authorization", "Bearer " + api_key)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=float(settings["laya_timeout"])) as resp:
                raw = resp.read(256 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            reason = {401: "Laya rejected the API key"}.get(exc.code, f"Laya answered HTTP {exc.code}")
            return self._fail(reason)
        except (TimeoutError, OSError) as exc:
            text = str(getattr(exc, "reason", exc)) or exc.__class__.__name__
            if "timed out" in text.lower() or isinstance(exc, TimeoutError):
                text = f"no answer within {settings['laya_timeout']:g}s"
            return self._fail(f"cannot reach Laya ({text})")
        latency = int((time.monotonic() - started) * 1000)
        if len(raw) > 256 * 1024:
            return self._fail("Laya's answer was unexpectedly large")
        try:
            score, model = parse_response(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            return self._fail(f"unexpected answer from Laya: {exc}")
        with self._lock:
            self.last_ok_at = time.time()
            self.last_latency_ms = latency
            self.blocked_until = 0.0
        return LayaResult(ok=True, score=score, model=model, latency_ms=latency)

    def health(self, settings: dict, timeout: float = 2.0) -> dict:
        base_url, api_key, why_not = self._resolve(settings)
        if base_url is None:
            return {"ok": False, "error": why_not or "Laya is not set up"}
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=timeout) as resp:
                data = json.loads(resp.read(64 * 1024).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": str(getattr(exc, "reason", exc))}
        if not isinstance(data, dict) or data.get("status") != "ok":
            return {"ok": False, "error": "unexpected health answer"}
        loaded = data.get("loaded")
        return {
            "ok": True,
            "loaded": [str(x)[:40] for x in loaded][:5] if isinstance(loaded, list) else [],
            "device": str(data.get("device", ""))[:40],
        }
