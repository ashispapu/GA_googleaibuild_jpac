"""Network settings for LLM calls: proxy on/off without code changes.

Resolution order (first match wins):
  1. `[llm] proxy` in agents.ini
  2. env `LLM_PROXY` (e.g. in .env)
  3. the standard env vars HTTPS_PROXY / HTTP_PROXY (either case; .env or shell)
  4. nothing set -> the OS proxy settings (on Windows: Internet Options / registry)

Values: a URL (`http://127.0.0.1:3128`) = use that proxy; `none` / `direct` / `off` = no proxy at all, even if the
OS has one configured. LiteLLM (aiohttp and httpx) reads the proxy from the environment, so the chosen value is written
to HTTP_PROXY/HTTPS_PROXY in both cases (lower-case wins in urllib, so both must agree).
"""
from __future__ import annotations

import logging
import os
import re
import urllib.request

log = logging.getLogger("plantguard.llm.network")

_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
_DIRECT = {"none", "direct", "off", "false", "no", "0"}
_LOCAL = "localhost,127.0.0.1,::1"


def _mask(url: str | None) -> str:
    """Hide a password in user:pass@host URLs before logging."""
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", url) if url else ""


def _set_no_proxy(value: str) -> None:
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = value


def apply_proxy(configured: str | None = None) -> str:
    """Apply the proxy choice to this process and return a one-line description (for the startup log)."""
    # LiteLLM fetches a price list from GitHub on import; behind a proxy that only produces a warning, so use the
    # bundled copy unless the user asked otherwise.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    choice = (configured or "").strip() or (os.getenv("LLM_PROXY") or "").strip()
    if choice.lower() in _DIRECT:
        for k in _PROXY_VARS:
            os.environ.pop(k, None)
        _set_no_proxy("*")  # also overrides the OS proxy (urllib skips the registry when any *_proxy env var is set)
        return "direct (no proxy)"
    if choice:
        source = "[llm] proxy" if (configured or "").strip() else "LLM_PROXY"
        url = choice
    else:
        url, source = None, ""
        for k in _PROXY_VARS:
            if os.getenv(k):
                url, source = os.environ[k], k
                break
    if url:
        if "://" not in url:
            url = f"http://{url}"
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            os.environ[k] = url
        existing = os.getenv("NO_PROXY") or os.getenv("no_proxy") or ""
        if existing.strip() != "*":
            merged = ",".join(dict.fromkeys(x.strip() for x in f"{existing},{_LOCAL}".split(",") if x.strip()))
            _set_no_proxy(merged)
        return f"proxy {_mask(url)} (from {source})"
    try:
        os_proxy = urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http")
    except (OSError, ValueError):  # pragma: no cover - platform quirks (registry access)
        os_proxy = None
    if os_proxy:
        return (f"proxy {_mask(os_proxy)} (from the OS settings; set HTTPS_PROXY or LLM_PROXY in .env to change it, "
                f"or LLM_PROXY=none to bypass it)")
    return "direct (no proxy configured)"


def proxy_hint(message: str) -> str:
    """Add a short, actionable hint to proxy-related connection errors."""
    m = message.lower()
    if "407" in m or "proxy authentication" in m:
        return (message + " | hint: the proxy wants a login. Point the LLM at your local auth proxy, e.g. "
                "HTTPS_PROXY=http://127.0.0.1:3128 in .env (or LLM_PROXY=none off the corporate network).")
    if "proxy" in m and any(w in m for w in ("connect", "refused", "unreachable", "timed out", "cannot")):
        return message + " | hint: the configured proxy is not reachable. Off the office network, comment out the proxy " \
                         "lines in .env (or set LLM_PROXY=none)."
    return message
