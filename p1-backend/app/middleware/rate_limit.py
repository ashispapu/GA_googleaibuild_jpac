"""Per-client token bucket (BE-9). Pure ASGI so it never buffers or touches WebSockets."""
import time
from typing import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from plantguard_contracts.schemas.common import ErrorCode

from app.core.errors import error_response


class TokenBucketLimiter:
    """Classic token bucket: `burst` tokens, refilled at `per_minute`/60 per second, one token per request."""

    def __init__(self, per_minute: int, burst: int, clock: Callable[[], float] = time.monotonic):
        """per_minute: sustained rate; burst: bucket size; clock: injectable monotonic clock (tests)."""
        self._rate, self._burst, self._clock = per_minute / 60.0, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last)

    def acquire(self, key: str) -> float:
        """0.0 if allowed, otherwise seconds to wait."""
        now = self._clock()
        tokens, last = self._buckets.get(key, (float(self._burst), now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return 0.0
        self._buckets[key] = (tokens, now)
        return (1 - tokens) / self._rate


class RateLimitMiddleware:
    """ASGI middleware that answers 429 RATE_LIMITED when a client's bucket is empty."""

    def __init__(self, app: ASGIApp, limiter: TokenBucketLimiter):
        """app: the wrapped ASGI app; limiter: the shared TokenBucketLimiter."""
        self.app, self.limiter = app, limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        """HTTP only: key = Authorization header (else client IP), 429 with Retry-After when empty."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        key = headers.get(b"authorization", b"").decode() or (scope.get("client") or ("anon",))[0]
        wait = self.limiter.acquire(key)
        if wait > 0:
            resp = error_response(429, ErrorCode.RATE_LIMITED, "Too many requests", {"retry_after_s": round(wait, 2)},
                                  headers={"Retry-After": str(max(1, int(wait + 0.999)))})
            return await resp(scope, receive, send)
        await self.app(scope, receive, send)
