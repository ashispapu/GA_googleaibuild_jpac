"""LiteLLM provider: one adapter for Gemini (default), OpenAI, Anthropic, Ollama... chosen by `[llm] model` in the .ini.

Gemini: model = gemini/<model-name>, key in the env var named by `api_key_env` (GEMINI_API_KEY).
Thinking: `reasoning_effort` (low|medium|high|disable) or `thinking_budget_tokens`; LiteLLM streams the model's
thought summary as `delta.reasoning_content`, which we surface as `reasoning` chunks.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from llm.network import apply_proxy, proxy_hint
from llm.types import LLMChunk, LLMRequest, LLMUnavailable, ToolCallRequest

log = logging.getLogger("plantguard.llm.litellm")


def build_kwargs(req: LLMRequest) -> dict[str, Any]:
    c = req.config
    model, vertex = c.provider_model()  # gemini/... -> vertex_ai/... when Vertex AI is switched on
    kw: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": req.system}, *req.messages],
        "stream": True,
        "timeout": c.timeout_s,
        "num_retries": c.num_retries,
    }
    if req.tools:
        kw["tools"] = req.tools
        kw["tool_choice"] = "auto"
    if c.temperature is not None:
        kw["temperature"] = c.temperature
    if c.max_tokens:
        kw["max_tokens"] = c.max_tokens
    key = c.api_key()
    if key:
        kw["api_key"] = key
    if c.api_base:
        kw["api_base"] = c.api_base
    if c.thinking_budget_tokens:
        kw["thinking"] = {"type": "enabled", "budget_tokens": c.thinking_budget_tokens}
    elif c.reasoning_effort:
        kw["reasoning_effort"] = c.reasoning_effort
    kw["stream_options"] = {"include_usage": True}
    kw.update(vertex)
    kw.update(c.extra_params or {})
    return kw


def _get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


_IMPORT_LOCK = __import__("threading").Lock()


def import_litellm():
    """Import LiteLLM once, thread-safely (a startup warm-up thread and the first agent call must not import it at the
    same time: Python would hand one of them a half-initialised module)."""
    with _IMPORT_LOCK:
        import litellm
        litellm.drop_params = True  # providers ignore params they don't support instead of failing
        litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banners in the server log
        for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        return litellm


class LiteLLMProvider:
    """Streams one completion through litellm.acompletion and normalises it into LLMChunks."""

    def __init__(self, completion_fn=None):
        self._completion_fn = completion_fn  # injectable for tests; defaults to litellm.acompletion
        self._proxy_applied: tuple[bool, str | None] = (False, None)

    def _fn(self, proxy: str | None = None):
        if self._completion_fn is not None:
            return self._completion_fn
        if self._proxy_applied != (True, proxy):  # once per proxy setting, before litellm is imported
            log.info("LLM network: %s", apply_proxy(proxy))
            self._proxy_applied = (True, proxy)
        return import_litellm().acompletion  # imported lazily so the rest of the layer works without it installed

    async def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]:
        kw = build_kwargs(req)
        if req.config.needs_key() and not req.config.api_key():  # Vertex / empty api_key_env: auth handled elsewhere
            raise LLMUnavailable(f"no API key: set {req.config.api_key_env} (see [llm] api_key_env)")
        try:
            response = await self._fn(req.config.proxy)(**kw)
        except LLMUnavailable:
            raise
        except Exception as e:  # auth, quota, network, bad params
            raise LLMUnavailable(proxy_hint(f"{type(e).__name__}: {e}")) from e

        calls: dict[int, dict[str, Any]] = {}
        finish, usage = None, None
        try:
            async for chunk in response:
                u = _get(chunk, "usage")
                if u:
                    usage = {k: int(_get(u, k, 0) or 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
                choices = _get(chunk, "choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish = _get(choice, "finish_reason") or finish
                delta = _get(choice, "delta")
                if delta is None:
                    continue
                reasoning = _get(delta, "reasoning_content")
                if reasoning:
                    yield LLMChunk("reasoning", text=reasoning)
                content = _get(delta, "content")
                if content:
                    yield LLMChunk("text", text=content)
                for tc in _get(delta, "tool_calls") or []:
                    idx = _get(tc, "index", None)
                    if idx is None:
                        idx = len(calls)
                    slot = calls.setdefault(idx, {"id": None, "name": "", "args": "", "extra": {}})
                    if _get(tc, "id"):
                        slot["id"] = _get(tc, "id")
                    fn = _get(tc, "function")
                    if fn is not None:
                        if _get(fn, "name"):
                            slot["name"] = _get(fn, "name")
                        if _get(fn, "arguments"):
                            slot["args"] += _get(fn, "arguments")
                    extra = _get(tc, "provider_specific_fields")
                    if extra:
                        slot["extra"].update(extra)
        except LLMUnavailable:
            raise
        except Exception as e:
            raise LLMUnavailable(proxy_hint(f"stream broke: {type(e).__name__}: {e}")) from e

        for i, slot in sorted(calls.items()):
            raw = slot["args"] or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = {"value": args}
            except json.JSONDecodeError:
                log.warning("tool call %s had invalid JSON args: %s", slot["name"], raw[:200])
                args = {"__invalid_json__": raw}
            yield LLMChunk("tool_call", tool_call=ToolCallRequest(
                id=slot["id"] or f"call_{i}", name=slot["name"], arguments=args, raw_arguments=raw,
                provider_fields=slot["extra"]))
        yield LLMChunk("done", finish_reason=finish, usage=usage)
