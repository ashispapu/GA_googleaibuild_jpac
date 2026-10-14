"""LiteLLM adapter: request building for Gemini and parsing of real LiteLLM stream chunks (reasoning, text,
fragmented tool calls), plus redaction in the client. No network: acompletion is replaced by a fake stream."""
import asyncio

import pytest

from guard.redactor import RedactionVault
from harness.config import LLMConfig
from llm.client import LLMClient
from llm.providers.litellm_provider import LiteLLMProvider, build_kwargs
from llm.types import LLMRequest, LLMUnavailable

litellm = pytest.importorskip("litellm")
from litellm.types.utils import ChatCompletionDeltaToolCall, Delta, Function, ModelResponseStream, StreamingChoices  # noqa: E402


def chunk(**delta):
    finish = delta.pop("finish", None)
    return ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(**delta), finish_reason=finish)])


def fake_stream(chunks, seen):
    async def acompletion(**kw):
        seen.update(kw)

        async def gen():
            for c in chunks:
                yield c
        return gen()
    return acompletion


CHUNKS = [
    chunk(reasoning_content="Thinking about "), chunk(reasoning_content="the spindle."),
    chunk(content="Let me check "), chunk(content="<PERSON_NAME_1>'s shift."),
    chunk(tool_calls=[ChatCompletionDeltaToolCall(index=0, id="call_a", type="function",
                                                  function=Function(name="get_asset_health", arguments='{"asset_'))]),
    chunk(tool_calls=[ChatCompletionDeltaToolCall(index=0, function=Function(arguments='id": "EQ-3012"}'))]),
    chunk(tool_calls=[ChatCompletionDeltaToolCall(index=1, id="call_b", type="function",
                                                  function=Function(name="find_technicians",
                                                                    arguments='{"skill": "<PERSON_NAME_1>"}'))]),
    chunk(finish="tool_calls"),
]


def test_gemini_kwargs(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k-123")
    cfg = LLMConfig(model="gemini/gemini-2.5-flash", reasoning_effort="low", temperature=0.1, max_tokens=500,
                    extra_params={"top_p": 0.9})
    kw = build_kwargs(LLMRequest("monitor", "SYS", [{"role": "user", "content": "hi"}],
                                 [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}], cfg))
    assert kw["model"] == "gemini/gemini-2.5-flash" and kw["api_key"] == "k-123" and kw["stream"] is True
    assert kw["reasoning_effort"] == "low" and "thinking" not in kw and kw["top_p"] == 0.9
    assert kw["messages"][0] == {"role": "system", "content": "SYS"} and kw["tool_choice"] == "auto"
    kw2 = build_kwargs(LLMRequest("m", "s", [], [], LLMConfig(thinking_budget_tokens=1024)))
    assert kw2["thinking"] == {"type": "enabled", "budget_tokens": 1024} and "reasoning_effort" not in kw2
    assert "tools" not in kw2


def test_stream_parsing_and_restore(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen: dict = {}
    reports = []
    client = LLMClient(provider=LiteLLMProvider(fake_stream(CHUNKS, seen)), redaction_sink=reports.append)
    vault = RedactionVault()

    async def run():
        return [c async for c in client.stream_chat(
            "scheduler", "You plan. Tech Suresh is on nights.", [{"role": "user", "content": "Ask Suresh at 10.1.2.3"}],
            [], LLMConfig(), vault)]
    out = asyncio.run(run())
    kinds = [c.kind for c in out]
    assert kinds[:2] == ["reasoning", "reasoning"] and kinds[-1] == "done"
    assert "".join(c.text for c in out if c.kind == "text") == "Let me check <PERSON_NAME_1>'s shift."
    calls = [c.tool_call for c in out if c.kind == "tool_call"]
    assert [(c.id, c.name) for c in calls] == [("call_a", "get_asset_health"), ("call_b", "find_technicians")]
    assert calls[0].arguments == {"asset_id": "EQ-3012"}  # fragments re-assembled
    assert calls[1].arguments == {"skill": "Suresh"}       # placeholders restored in tool args
    sent = str(seen["messages"])
    assert "Suresh" not in sent and "10.1.2.3" not in sent and "<PERSON_NAME_1>" in sent
    assert len(reports) == 1 and {f.kind for f in reports[0].findings} == {"person_name", "ip_address"}
    assert vault.restore("<PERSON_NAME_1>") == "Suresh"


def test_provider_errors_become_llm_unavailable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    calls, slept = [], []

    async def boom(**kw):
        calls.append(1)
        raise RuntimeError("429 quota")

    async def no_sleep(s):
        slept.append(s)
    client = LLMClient(provider=LiteLLMProvider(boom), sleep=no_sleep)

    async def run():
        return [c async for c in client.stream_chat("monitor", "s", [], [], LLMConfig(retry_max_attempts=3))]
    with pytest.raises(LLMUnavailable, match="quota"):
        asyncio.run(run())
    assert len(calls) == 3 and len(slept) == 2  # rate limit -> retried with backoff, then given up


def test_missing_gemini_key_fails_fast(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = LLMClient(provider=LiteLLMProvider(fake_stream(CHUNKS, {})))

    async def run():
        return [c async for c in client.stream_chat("monitor", "s", [], [], LLMConfig())]
    with pytest.raises(LLMUnavailable, match="GEMINI_API_KEY"):
        asyncio.run(run())


def test_real_litellm_mock_response_streams():
    """Through the real litellm.acompletion (its built-in mock_response, no network)."""
    cfg = LLMConfig(model="gemini/gemini-2.5-flash", api_key_env="", extra_params={"mock_response": "Hello plant floor"},
                    reasoning_effort=None)
    client = LLMClient(provider=LiteLLMProvider())

    async def run():
        return [c async for c in client.stream_chat("monitor", "s", [{"role": "user", "content": "hi"}], [], cfg)]
    out = asyncio.run(run())
    assert "".join(c.text for c in out if c.kind == "text") == "Hello plant floor"
    assert out[-1].kind == "done"
