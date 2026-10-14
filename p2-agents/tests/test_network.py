"""Proxy switching for LLM calls (llm/network.py) and that LiteLLM really goes through the chosen proxy."""
import asyncio
import os
import socket
import threading
import urllib.request

import pytest
from llm.network import apply_proxy, proxy_hint

VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
        "LLM_PROXY")


@pytest.fixture(autouse=True)
def clean_env():
    saved = dict(os.environ)
    for k in VARS:
        os.environ.pop(k, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_env_proxy_is_used_and_cases_agree():
    os.environ["https_proxy"] = "http://stale-lowercase:8080"  # e.g. machine-wide, lower-case
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:3128"        # from .env
    desc = apply_proxy(None)
    assert "127.0.0.1:3128" in desc and "HTTPS_PROXY" in desc
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        assert os.environ[k] == "http://127.0.0.1:3128"
    assert urllib.request.getproxies()["https"] == "http://127.0.0.1:3128"
    assert {"localhost", "127.0.0.1"} <= set(os.environ["NO_PROXY"].split(","))
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"


def test_ini_value_wins_and_scheme_is_added():
    os.environ["HTTPS_PROXY"] = "http://from-env:1"
    os.environ["LLM_PROXY"] = "http://from-llm-proxy:2"
    assert "from [llm] proxy" in apply_proxy("10.0.0.5:3128")
    assert os.environ["https_proxy"] == "http://10.0.0.5:3128"


def test_llm_proxy_env_beats_standard_vars():
    os.environ["HTTPS_PROXY"] = "http://from-env:1"
    os.environ["LLM_PROXY"] = "http://user:secret@corp:3128"
    desc = apply_proxy(None)
    assert "secret" not in desc and "user:***@corp:3128" in desc
    assert os.environ["HTTPS_PROXY"] == "http://user:secret@corp:3128"


@pytest.mark.parametrize("word", ["none", "direct", "off"])
def test_direct_bypasses_every_proxy(word):
    os.environ["HTTPS_PROXY"] = os.environ["http_proxy"] = "http://rb-proxy-in.example:8080"
    os.environ["LLM_PROXY"] = word
    assert apply_proxy(None).startswith("direct")
    assert not any(os.getenv(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"))
    assert "https" not in urllib.request.getproxies()  # env present ('no') -> OS/registry proxy is not consulted
    assert urllib.request.proxy_bypass("generativelanguage.googleapis.com")


def test_proxy_hints():
    assert "3128" in proxy_hint("APIConnectionError - 407, message='Proxy authentication required'")
    assert "comment out" in proxy_hint("Cannot connect to host proxy 127.0.0.1:3128 [Connection refused]")
    assert proxy_hint("rate limited") == "rate limited"


class _Proxy:
    """Tiny HTTP proxy that records CONNECT targets and answers 407 (like the corporate proxy without a login)."""

    def __init__(self):
        self.seen: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(4096).decode("latin-1")
                self.seen.append(data.split("\r\n", 1)[0])
                conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                             b"Proxy-Authenticate: Basic realm=\"x\"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


def test_litellm_really_uses_the_proxy():
    """End to end through the real LiteLLM provider: the Gemini call goes to the proxy we configured."""
    pytest.importorskip("litellm")
    from harness.config import LLMConfig
    from llm.providers.litellm_provider import LiteLLMProvider
    from llm.types import LLMRequest, LLMUnavailable

    proxy = _Proxy()
    os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{proxy.port}"
    os.environ["GEMINI_API_KEY_TEST"] = "dummy"
    cfg = LLMConfig(api_key_env="GEMINI_API_KEY_TEST", timeout_s=5, num_retries=0, reasoning_effort=None)
    req = LLMRequest(agent="monitor", system="s", messages=[{"role": "user", "content": "hi"}], tools=[], config=cfg)

    async def go():
        async for _ in LiteLLMProvider().stream(req):
            pass

    with pytest.raises(LLMUnavailable) as e:
        asyncio.run(go())
    assert any("generativelanguage.googleapis.com" in s for s in proxy.seen), proxy.seen
    assert "407" in str(e.value) and "hint" in str(e.value)
