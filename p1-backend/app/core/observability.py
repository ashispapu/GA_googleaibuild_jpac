"""Logging, request tracing and HTTP hardening for production (Cloud Run + Cloud Logging + Cloud Trace).

- LOG_FORMAT=json (default on Cloud Run, detected by K_SERVICE): one JSON object per line with `severity`, `message`,
  `logger`, and the request's trace id, so Cloud Logging groups every log line under its request.
- RequestContextMiddleware: request id (X-Request-ID, generated when absent), trace correlation from
  X-Cloud-Trace-Context / traceparent, one access-log line per request with latency, and security headers.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace", default=None)
access_log = logging.getLogger("plantguard.access")


def environment() -> str:
    """PLANTGUARD_ENV: dev | staging | prod."""
    return os.getenv("PLANTGUARD_ENV", "dev").strip().lower() or "dev"


def is_production() -> bool:
    return environment() in ("prod", "production")


class JsonFormatter(logging.Formatter):
    def __init__(self, project: str | None):
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        out = {"severity": record.levelname if record.levelname != "WARNING" else "WARNING",
               "message": record.getMessage(), "logger": record.name,
               "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z"}
        rid, trace = _request_id.get(), _trace.get()
        if rid:
            out["request_id"] = rid
        if trace and self.project:
            out["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace}"
        if record.exc_info:
            out["stack_trace"] = self.formatException(record.exc_info)
        for k in ("http", "role"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        return json.dumps(out, default=str)


_SECRET = re.compile(r"((?:access_)?token=|Bearer\s+|api[-_]key[=:]\s*)[^&\s\"']+", re.I)


class RedactSecrets(logging.Filter):
    """Never write bearer tokens to the logs (WebSocket URLs carry ?token=...)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if _SECRET.search(msg):
            record.msg, record.args = _SECRET.sub(r"\1[redacted]", msg), ()
        return True


def setup_logging() -> None:
    fmt = os.getenv("LOG_FORMAT") or ("json" if os.getenv("K_SERVICE") else "text")
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if getattr(root, "_plantguard", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter(os.getenv("GOOGLE_CLOUD_PROJECT")))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets())
    root.handlers = [handler]
    root.setLevel(level)
    root._plantguard = True  # type: ignore[attr-defined]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):  # one format, one redaction for everything
        lg = logging.getLogger(name)
        lg.handlers, lg.propagate = [], True
    for noisy in ("uvicorn.access", "httpx", "LiteLLM", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]
_DOCS = (b"/docs", b"/redoc", b"/openapi.json")


class RequestContextMiddleware:
    """Pure ASGI (never buffers bodies or touches WebSocket frames)."""

    def __init__(self, app: ASGIApp, hsts: bool = False):
        self.app, self.hsts = app, hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode()[:64] or uuid.uuid4().hex
        trace = None
        if b"x-cloud-trace-context" in headers:
            trace = headers[b"x-cloud-trace-context"].decode().split("/")[0]
        elif b"traceparent" in headers:
            parts = headers[b"traceparent"].decode().split("-")
            trace = parts[1] if len(parts) > 2 else None
        t_rid, t_trace = _request_id.set(rid), _trace.set(trace)
        started = time.perf_counter()
        status = {"code": 0}
        path = scope.get("path", "")

        async def send_wrapper(message: Message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                h = list(message.get("headers", []))
                h.append((b"x-request-id", rid.encode()))
                for k, v in SECURITY_HEADERS:
                    if k == b"content-security-policy" and path.encode().startswith(_DOCS):
                        continue  # Swagger UI needs its scripts
                    h.append((k, v))
                if self.hsts:
                    h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                h.append((b"cache-control", b"no-store"))
                message["headers"] = h
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper if scope["type"] == "http" else send)
        finally:
            if scope["type"] == "http" and path not in ("/healthz", "/readyz"):
                ms = (time.perf_counter() - started) * 1000
                access_log.info("%s %s %s %.0fms", scope.get("method"), path, status["code"], ms,
                                extra={"http": {"method": scope.get("method"), "path": path, "status": status["code"],
                                                "latency_ms": round(ms, 1)}})
            _request_id.reset(t_rid)
            _trace.reset(t_trace)
