"""Exponential backoff for LLM calls (llm/retry.py + LLMClient.stream_chat) and how a run shows the waits."""
import asyncio
import random

import pytest
import scripts
from harness.config import LLMConfig
from llm.client import LLMClient
from llm.retry import RequestPacer, RetryPolicy, classify
from llm.types import LLMChunk, LLMUnavailable
from support import collect, make_incident, make_runtime, with_overrides

from plantguard_contracts.schemas.incidents import EventType

# the real error from Gemini's free tier (shortened)
GEMINI_429 = ("stream broke: MidStreamFallbackError: litellm.MidStreamFallbackError: litellm.RateLimitError: "
              "vertex_ai_betaException - b'{\"error\": {\"code\": 429, \"message\": \"You exceeded your current quota, "
              "please check your plan and billing details. * Quota exceeded for metric: generativelanguage.googleapis"
              ".com/generate_content_free_tier_requests, limit: 5, model: gemini-2.5-flash\\nPlease retry in "
              "1.539150579s.\", \"status\": \"RESOURCE_EXHAUSTED\", \"quotaId\": "
              "\"GenerateRequestsPerMinutePerProjectPerModel-FreeTier\", \"retryDelay\": \"1s\"}}'")


# ---------------------------------------------------------------- classification
def test_gemini_free_tier_429_is_retryable_with_hint():
    f = classify(GEMINI_429)
    assert f.retryable and f.reason == "rate limit" and f.retry_after == pytest.approx(1.539150579)


@pytest.mark.parametrize("msg,retryable", [
    ("APIConnectionError: 503 Service Unavailable", True),
    ("InternalServerError: 500", True),
    ("Timeout: request timed out after 20s", True),
    ("APIConnectionError: Server disconnected", True),
    ("model is overloaded, try again later", True),
    ("AuthenticationError: API key not valid. Please pass a valid API key.", False),
    ("no API key: set GEMINI_API_KEY (see [llm] api_key_env)", False),
    ("APIConnectionError - 407, message='Proxy authentication required'", False),
    ("BadRequestError: 400 INVALID_ARGUMENT", False),
    ("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier", False),  # daily quota
    ("scripted failure", False),
])
def test_classify(msg, retryable):
    assert classify(msg).retryable is retryable


def test_backoff_doubles_is_capped_and_respects_hint():
    p = RetryPolicy(base_s=2, max_s=30, jitter=0)
    assert [p.delay(n) for n in (1, 2, 3, 4, 5, 6)] == [2, 4, 8, 16, 30, 30]
    assert p.delay(1, hint=10) == 10.5           # server asked for more
    assert p.delay(4, hint=1.5) == 16            # backoff already longer than the hint
    j = RetryPolicy(base_s=10, max_s=60, jitter=0.25)
    rng = random.Random(1)
    assert all(7.5 <= j.delay(1, rng=rng) <= 12.5 for _ in range(50))


# ---------------------------------------------------------------- client retry loop
class Flaky:
    """Fails `fails` times with `error`, then streams a short answer. `partial` streams text before failing."""

    def __init__(self, fails, error=GEMINI_429, partial=False):
        self.fails, self.error, self.partial, self.calls = fails, error, partial, 0

    async def stream(self, req):
        self.calls += 1
        if self.calls <= self.fails:
            if self.partial:
                yield LLMChunk("text", text="half an ans")
            raise LLMUnavailable(self.error)
        yield LLMChunk("text", text="ok")
        yield LLMChunk("done", finish_reason="stop")


def _run(client, cfg, waits=None):
    async def hook(sec, reason, attempt, max_attempts):
        waits.append((round(sec, 2), reason, attempt, max_attempts))

    async def go():
        return [c async for c in client.stream_chat("monitor", "sys", [{"role": "user", "content": "hi"}], [], cfg,
                                                     on_wait=hook if waits is not None else None)]
    return asyncio.run(go())


def _client(provider):
    slept = []

    async def fake_sleep(s):
        slept.append(round(s, 2))
    return LLMClient(provider=provider, sleep=fake_sleep), slept


CFG = LLMConfig(provider="x", retry_max_attempts=5, retry_base_s=2, retry_max_s=60)


def test_retries_rate_limit_then_succeeds():
    prov = Flaky(fails=3, error="RateLimitError: 429 Too Many Requests")
    client, slept = _client(prov)
    waits = []
    chunks = _run(client, CFG, waits)
    assert [c.text for c in chunks if c.kind == "text"] == ["ok"] and prov.calls == 4
    assert len(slept) == 3 and slept[0] < slept[1] < slept[2]            # exponential (jitter +/-25 %)
    assert 1.5 <= slept[0] <= 2.5 and 6 <= slept[2] <= 10
    assert [w[2] for w in waits] == [2, 3, 4] and all(w[1] == "rate limit" and w[3] == 5 for w in waits)


def test_server_hint_sets_the_minimum_wait():
    prov = Flaky(fails=1)
    client, slept = _client(prov)
    _run(client, LLMConfig(provider="x", retry_base_s=0.1, retry_max_s=60))
    assert slept == [pytest.approx(2.04, abs=0.01)]  # 1.539 s hint + 0.5 s margin beats the 0.1 s backoff


def test_gives_up_after_max_attempts():
    prov = Flaky(fails=99)
    client, slept = _client(prov)
    with pytest.raises(LLMUnavailable, match="^rate limit: "):
        _run(client, LLMConfig(provider="x", retry_max_attempts=3))
    assert prov.calls == 3 and len(slept) == 2


def test_non_retryable_fails_at_once():
    prov = Flaky(fails=1, error="AuthenticationError: API key not valid")
    client, slept = _client(prov)
    with pytest.raises(LLMUnavailable):
        _run(client, CFG)
    assert prov.calls == 1 and slept == []


def test_no_retry_once_text_was_streamed():
    """Retrying after partial output would show duplicated text in the UI."""
    prov = Flaky(fails=1, partial=True)
    client, slept = _client(prov)
    with pytest.raises(LLMUnavailable):
        _run(client, CFG)
    assert prov.calls == 1 and slept == []


def test_retry_disabled_with_one_attempt():
    prov = Flaky(fails=1)
    client, _ = _client(prov)
    with pytest.raises(LLMUnavailable):
        _run(client, LLMConfig(provider="x", retry_max_attempts=1))
    assert prov.calls == 1


# ---------------------------------------------------------------- pacing
def test_pacer_keeps_under_requests_per_minute():
    now = [0.0]
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        now[0] += s

    pacer = RequestPacer(2, clock=lambda: now[0], sleep=fake_sleep)

    async def go():
        for t in (0, 1, 2):  # third call within the same minute must wait for the first to age out
            now[0] = max(now[0], t)
            await pacer.acquire()
    asyncio.run(go())
    assert len(slept) == 1 and slept[0] == pytest.approx(58.05)
    assert RequestPacer(0).wait_needed() == 0


# ---------------------------------------------------------------- whole run
def test_full_run_survives_rate_limits_and_shows_waits(cfg, mock_tools):
    """Every agent's first model call gets a 429; the run still finishes live with the C9 numbers, and the waits
    appear as STATUS events (plain words, never 'finished')."""
    seen: set[str] = set()

    def flaky_story(req):
        from llm.providers.scripted import Turn
        if req.agent not in seen:
            seen.add(req.agent)
            return Turn(fail=True)
        return scripts.story(req)

    fast = with_overrides(cfg, llm={"retry_base_s": 0.001, "retry_max_s": 0.002, "retry_max_attempts": 3})
    rt, _ = make_runtime(fast, mock_tools, flaky_story)

    import llm.providers.scripted as sp
    original = sp.LLMUnavailable

    class RateLimited(original):
        def __init__(self, _msg="scripted failure"):
            super().__init__("RateLimitError: 429 RESOURCE_EXHAUSTED")
    sp.LLMUnavailable = RateLimited
    try:
        evs = asyncio.run(collect(rt, make_incident()))
    finally:
        sp.LLMUnavailable = original

    waits = [e for e in evs if e.type == EventType.STATUS and "trying again" in e.message]
    assert len(waits) == len(seen) >= 6
    assert all("rate limit" in e.message and "try 2 of 3" in e.message for e in waits)
    results = {e.agent.value: e.result for e in evs if e.type == EventType.RESULT}
    assert {o.id: o.cost.total.amount for o in results["decision"].options} == {"OPT-A": 22385, "OPT-B": 2485,
                                                                                   "OPT-C": 33485}
    assert not [e for e in evs if e.type == EventType.ERROR]
    assert evs[-1].message == "finished"


def test_daily_quota_pauses_the_model_without_retries():
    """A 'quota exceeded for the day' 429 pauses the model: no retries now, and the next call fails fast offline."""
    from llm import client as client_mod

    class Daily:
        calls = 0

        async def stream(self, req):
            Daily.calls += 1
            raise RuntimeError('RateLimitError 429 RESOURCE_EXHAUSTED "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier" '
                               '"retryDelay": "54598s"')
            yield  # pragma: no cover

    client_mod._PAUSED.clear()
    client, slept = _client(Daily())
    cfg = LLMConfig(provider="x", model="gemini/test-daily", retry_max_attempts=5)
    with pytest.raises(LLMUnavailable, match="AI paused until"):
        _run(client, cfg)
    with pytest.raises(LLMUnavailable, match="AI paused until"):
        _run(client, cfg)
    assert Daily.calls == 1 and slept == []
    client_mod._PAUSED.clear()
