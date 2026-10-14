"""LLM client (AG-1). Agents ONLY talk to models through this module (C4).

- Provider and model come from config/agents.ini ([llm], per-agent overrides in [agent.<name>]); LiteLLM by default,
  Gemini by default model (`gemini/...`, key in GEMINI_API_KEY).
- Every prompt is redacted first (C7). One RedactionReport per LLM call goes to `redaction_sink` (the backend's
  RedactionLog -> GET /security/redactions).
- `stream_chat()` yields reasoning/text deltas and whole tool calls; tool-call arguments are restored here (they are
  complete), streamed text is restored by the event emitter (it may split a placeholder across chunks).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Awaitable, Callable

from plantguard_contracts.schemas.security import RedactionReport

from guard.redactor import RedactionVault, RegexRedactor
from harness.config import LLMConfig
from llm.providers import load_provider
from llm.retry import RequestPacer, RetryPolicy, classify
from llm.types import LLMChunk, LLMProvider, LLMRequest, LLMUnavailable, ToolCallRequest

log = logging.getLogger("plantguard.llm")
RedactionSink = Callable[[RedactionReport], None]
# on_wait(seconds, reason, attempt, max_attempts): told before every backoff / pacing wait (UI status, logs)
WaitHook = Callable[[float, str, int, int], Awaitable[None]]
_PACERS: dict[tuple[str, int], RequestPacer] = {}  # shared by all runs in the process, per model


def _pacer(cfg: LLMConfig) -> RequestPacer | None:
    if cfg.requests_per_minute <= 0:
        return None
    key = (cfg.model, cfg.requests_per_minute)
    if key not in _PACERS:
        _PACERS[key] = RequestPacer(cfg.requests_per_minute)
    return _PACERS[key]

__all__ = ["LLMClient", "LLMUnavailable", "chat"]


def _restore_obj(obj, vault: RedactionVault):
    if isinstance(obj, str):
        return vault.restore(obj)
    if isinstance(obj, list):
        return [_restore_obj(x, vault) for x in obj]
    if isinstance(obj, dict):
        return {k: _restore_obj(v, vault) for k, v in obj.items()}
    return obj


# Model -> monotonic time until which it is not called (daily quota used up). Shared by every agent and run, so one
# 429 "quota exceeded for the day" pauses the LLM for everyone instead of each agent retrying and failing again.
_PAUSED: dict[str, tuple[float, str]] = {}
DEFAULT_QUOTA_PAUSE_S = 3600.0


def _short(text: str, n: int = 160) -> str:
    """One line for the log. Provider errors (Google's JSON body) are reduced to their own message + reason, which is
    what tells you what to fix ("Requests to this API ... are blocked. [API_KEY_SERVICE_BLOCKED]")."""
    raw = str(text)
    msg = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    reason = re.search(r'"reason"\s*:\s*"([A-Z_]+)"', raw)
    code = re.search(r'"code"\s*:\s*(\d{3})', raw)
    if msg:
        out = (f"HTTP {code.group(1)}: " if code else "") + msg.group(1).replace("\\n", " ")
        if reason:
            out += f" [{reason.group(1)}]"
        return " ".join(out.split())[:400]
    raw = " ".join(raw.split())
    return raw if len(raw) <= n else raw[: n - 1] + "…"


def llm_paused(model: str) -> str | None:
    """Plain-words reason while `model` is paused, else None."""
    p = _PAUSED.get(model)
    if p is None:
        return None
    until, why = p
    import time
    if time.monotonic() >= until:
        _PAUSED.pop(model, None)
        return None
    return why


def _pause(model: str, seconds: float, reason: str) -> None:
    import time
    first = model not in _PAUSED
    resume = datetime.now().astimezone().timestamp() + seconds
    when = datetime.fromtimestamp(resume).astimezone().strftime("%a %H:%M")
    why = f"{reason}; AI paused until {when}, agents run rule-based"
    _PAUSED[model] = (time.monotonic() + seconds, why)
    if first:
        log.warning("%s: %s", model, why)


class LLMClient:
    def __init__(self, provider: LLMProvider | None = None, redactor: RegexRedactor | None = None,
                 redaction_sink: RedactionSink | None = None, provider_factory=load_provider, sleep=asyncio.sleep):
        self._sleep = sleep  # injectable for tests
        self._provider = provider
        self._providers: dict[str, LLMProvider] = {}
        self._factory = provider_factory
        self._redactor = redactor or RegexRedactor()
        self._sink = redaction_sink

    def provider_for(self, cfg: LLMConfig) -> LLMProvider:
        if self._provider is not None:
            return self._provider
        if cfg.provider not in self._providers:
            self._providers[cfg.provider] = self._factory(cfg.provider)
        return self._providers[cfg.provider]

    def _redact(self, agent: str, system: str, messages: list[dict], cfg: LLMConfig,
                vault: RedactionVault) -> tuple[str, list[dict]]:
        if not cfg.redact:
            return system, messages
        findings, chars_in, chars_out = [], 0, 0

        def clean(text: str) -> str:
            nonlocal chars_in, chars_out
            out, f = self._redactor.redact_text(text, vault)
            findings.extend(f)
            chars_in += len(text)
            chars_out += len(out)
            return out

        sys_clean = clean(system)
        out = []
        for m in messages:
            m2 = dict(m)
            if isinstance(m2.get("content"), str):
                m2["content"] = clean(m2["content"])
            if m2.get("tool_calls"):
                calls = []
                for tc in m2["tool_calls"]:
                    tc = json.loads(json.dumps(tc))
                    tc["function"]["arguments"] = clean(tc["function"].get("arguments") or "")
                    calls.append(tc)
                m2["tool_calls"] = calls
            out.append(m2)
        report = RedactionReport(call_id=f"LLM-{uuid.uuid4().hex[:8]}", agent=agent, ts=datetime.now(timezone.utc),
                                 model=cfg.model, findings=findings, chars_in=chars_in, chars_out=chars_out)
        if self._sink:
            try:
                self._sink(report)
            except Exception:  # the security log must never break a run
                log.exception("redaction sink failed")
        return sys_clean, out

    async def stream_chat(self, agent: str, system: str, messages: list[dict], tools: list[dict] | None,
                          cfg: LLMConfig, vault: RedactionVault | None = None,
                          on_wait: WaitHook | None = None) -> AsyncIterator[LLMChunk]:
        """Stream one model call. Rate limits, overload, timeouts and dropped connections are retried with
        exponential backoff (llm/retry.py) as long as nothing was streamed yet; then LLMUnavailable is raised."""
        vault = vault if vault is not None else RedactionVault()
        sys_clean, msgs_clean = self._redact(agent, system, messages, cfg, vault)
        req = LLMRequest(agent=agent, system=sys_clean, messages=msgs_clean, tools=list(tools or []), config=cfg)
        policy = RetryPolicy(max_attempts=max(1, cfg.retry_max_attempts), base_s=cfg.retry_base_s,
                             max_s=cfg.retry_max_s)
        pacer = _pacer(cfg)
        paused = llm_paused(cfg.model)
        if paused:
            raise LLMUnavailable(paused)  # no network call, no retries
        attempt = 0
        while True:
            attempt += 1
            if pacer is not None:
                async def paced(w: float, _a=attempt) -> None:
                    log.info("%s: pacing %.1fs to stay under %d requests/min", agent, w, cfg.requests_per_minute)
                    if on_wait:
                        await on_wait(w, "pacing", _a, policy.max_attempts)
                await pacer.acquire(paced)
            streamed = False
            try:
                async for chunk in self.provider_for(cfg).stream(req):
                    if chunk.kind == "tool_call" and chunk.tool_call is not None:
                        tc = chunk.tool_call
                        chunk = LLMChunk("tool_call", tool_call=ToolCallRequest(
                            id=tc.id, name=tc.name, arguments=_restore_obj(tc.arguments, vault),
                            raw_arguments=vault.restore(tc.raw_arguments), provider_fields=tc.provider_fields))
                    if chunk.kind != "done":
                        streamed = True
                    yield chunk
                return
            except Exception as e:
                failure = classify(f"{type(e).__name__}: {e}")
                if failure.reason == "daily quota used up":
                    _pause(cfg.model, failure.retry_after or DEFAULT_QUOTA_PAUSE_S, "daily quota used up")
                    raise LLMUnavailable(llm_paused(cfg.model) or failure.reason) from e
                if streamed or not failure.retryable or attempt >= policy.max_attempts:
                    if attempt > 1:
                        log.warning("%s: giving up after %d tries (%s)", agent, attempt, failure.reason)
                    # short message: the provider's full error body (often kilobytes of JSON) stays out of the log
                    raise LLMUnavailable(f"{failure.reason}: {_short(e)}") from e
                wait = policy.delay(attempt, failure.retry_after)
                log.warning("%s: %s, retry %d/%d in %.1fs", agent, failure.reason, attempt + 1,
                            policy.max_attempts, wait)
                if on_wait:
                    await on_wait(wait, failure.reason, attempt + 1, policy.max_attempts)
                await self._sleep(wait)

    async def chat(self, agent: str, system: str, messages: list[dict], tools: list[dict] | None,
                   cfg: LLMConfig, vault: RedactionVault | None = None) -> dict:
        """Non-streaming convenience: {"text", "reasoning", "tool_calls": [{"name", "args"}]} (restored)."""
        vault = vault if vault is not None else RedactionVault()
        text, reasoning, calls = [], [], []
        async for ch in self.stream_chat(agent, system, messages, tools, cfg, vault):
            if ch.kind == "text":
                text.append(ch.text)
            elif ch.kind == "reasoning":
                reasoning.append(ch.text)
            elif ch.kind == "tool_call" and ch.tool_call:
                calls.append({"name": ch.tool_call.name, "args": ch.tool_call.arguments})
        return {"text": vault.restore("".join(text)), "reasoning": vault.restore("".join(reasoning)),
                "tool_calls": calls}


async def chat(agent: str, system: str, messages: list[dict], tools: list[dict] | None = None,
               cfg: LLMConfig | None = None) -> dict:
    """Back-compat helper from the starter: one round trip with the configured model."""
    if cfg is None:
        from harness.config import load_config
        conf = load_config()
        cfg = conf.llm_for(agent) if agent in conf.agents else conf.llm
    return await LLMClient().chat(agent, system, messages, tools, cfg)
