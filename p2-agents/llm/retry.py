"""Retry policy for LLM calls: exponential backoff with jitter, server hints, and client-side pacing.

- Only failures that can heal are retried: rate limits (429 / RESOURCE_EXHAUSTED), overload and 5xx, timeouts,
  dropped connections. Bad keys, bad requests, a proxy that wants a login, or a used-up *daily* quota fail at once.
- Delay for try n (n = 1, 2, ...): min(max_s, base_s * 2**(n-1)), +/- jitter; never shorter than the server's own
  "retry in N s" / RetryInfo.retryDelay hint.
- A call is retried only if nothing was streamed yet, so the UI never sees duplicated thinking/answer text.
- `RequestPacer` keeps calls under `[llm] requests_per_minute` (e.g. 5 on Gemini's free tier) instead of hitting 429s.
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from collections import deque
from dataclasses import dataclass

_NOT_RETRYABLE = (
    "no api key", "authentication", "permission", "api key not valid", "api_key_invalid", "invalid api key",
    "proxy authentication", "badrequest", "bad request", "invalid_argument", "notfound",
    "not found", "contextwindowexceeded", "context window", "content policy", "contentpolicy",
    "default credentials", "credentials were not found", "refresherror", "invalid_grant",  # Vertex AI sign-in
    "no module named",  # a missing package never fixes itself by retrying
)
_RETRYABLE = (
    "ratelimit", "rate limit", "resource_exhausted", "quota", "too many requests", "internalservererror", "internal server error", "serviceunavailable",
    "service unavailable", "unavailable", "overloaded", "timeout", "timed out", "apiconnectionerror",
    "connection reset", "connection aborted", "server disconnected", "temporarily", "try again",
)
_CODE_FATAL = re.compile(r"(?<![\d.])(400|401|403|404|407)(?![\d.])")
_CODE_RETRY = re.compile(r"(?<![\d.])(408|429|500|502|503|504)(?![\d.])")
_CODE_LIMIT = re.compile(r"(?<![\d.])429(?![\d.])")
# Gemini's free tier also has per-day quotas; waiting a minute will not help those.
_DAILY_QUOTA = re.compile(r"per\s*day|perday|requests_per_day|daily", re.IGNORECASE)
_HINTS = (
    re.compile(r"retry in\s+([\d.]+)\s*s", re.IGNORECASE),
    re.compile(r"retryDelay[\"']?\s*:\s*[\"']?([\d.]+)s", re.IGNORECASE),
    re.compile(r"retry[- ]after[\"']?\s*[:=]\s*[\"']?([\d.]+)", re.IGNORECASE),
)


@dataclass(frozen=True)
class Failure:
    retryable: bool
    reason: str                 # short, plain words for the UI / log
    retry_after: float | None   # server hint in seconds


def classify(message: str) -> Failure:
    m = message.lower()
    hint = None
    for rx in _HINTS:
        hit = rx.search(message)
        if hit:
            try:
                hint = float(hit.group(1))
                break
            except ValueError:
                pass
    is_limit = bool(_CODE_LIMIT.search(m)) or any(
        k in m for k in ("ratelimit", "rate limit", "resource_exhausted", "quota", "too many"))
    if is_limit and _DAILY_QUOTA.search(message) and "perminute" not in m.replace(" ", ""):
        return Failure(False, "daily quota used up", hint)
    if (any(k in m for k in _NOT_RETRYABLE) or _CODE_FATAL.search(m)) and not is_limit:
        return Failure(False, "request rejected", hint)
    if is_limit:
        return Failure(True, "rate limit", hint)
    if any(k in m for k in _RETRYABLE) or _CODE_RETRY.search(m):
        reason = "timeout" if ("timeout" in m or "timed out" in m) else \
                 "connection problem" if "connection" in m or "disconnected" in m else "service busy"
        return Failure(True, reason, hint)
    return Failure(False, "error", hint)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5          # total tries, including the first
    base_s: float = 2.0
    max_s: float = 60.0
    jitter: float = 0.25           # +/- 25 %

    def delay(self, attempt: int, hint: float | None = None, rng: random.Random | None = None) -> float:
        """Wait before try `attempt + 1` (attempt = tries done so far, >= 1)."""
        d = min(self.max_s, self.base_s * (2 ** (attempt - 1)))
        if self.jitter:
            d *= 1 + (rng or random).uniform(-self.jitter, self.jitter)
        if hint is not None:
            d = max(d, hint + 0.5)  # never earlier than the server asked (small margin)
        return max(0.0, d)


class RequestPacer:
    """Sliding 60 s window shared by every agent using the same model."""

    def __init__(self, per_minute: int, clock=time.monotonic, sleep=asyncio.sleep):
        self.per_minute = per_minute
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._clock, self._sleep = clock, sleep

    def wait_needed(self) -> float:
        if self.per_minute <= 0:
            return 0.0
        now = self._clock()
        while self._sent and now - self._sent[0] >= 60.0:
            self._sent.popleft()
        if len(self._sent) < self.per_minute:
            return 0.0
        return 60.0 - (now - self._sent[0]) + 0.05

    async def acquire(self, on_wait=None) -> None:
        if self.per_minute <= 0:
            return
        async with self._lock:
            while (w := self.wait_needed()) > 0:
                if on_wait:
                    await on_wait(w)
                await self._sleep(w)
            self._sent.append(self._clock())
