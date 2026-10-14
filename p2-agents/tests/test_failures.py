"""LLM failure policy and run budget."""
import asyncio

import pytest

from plantguard_contracts.schemas.incidents import EventType

import scripts
from llm.types import LLMUnavailable
from support import collect, make_incident, make_runtime, with_overrides


def test_raise_mode_propagates_for_backend_fallback(cfg, mock_tools):
    rt, _ = make_runtime(cfg, mock_tools, scripts.failing)
    got = []

    async def run():
        async for e in rt.run(make_incident()):
            got.append(e)

    with pytest.raises(LLMUnavailable):
        asyncio.run(run())
    assert got and got[0].message == "started"  # events sent before the failure were streamed


def test_degrade_mode_finishes_with_rule_based_results(cfg, mock_tools):
    rt, _ = make_runtime(with_overrides(cfg, runtime={"on_llm_failure": "degrade"}), mock_tools, scripts.failing)
    evs = asyncio.run(collect(rt, make_incident()))
    errors = [e for e in evs if e.type == EventType.ERROR]
    assert len(errors) == 1 and errors[0].message.startswith("LLM_UNAVAILABLE")
    results = {e.agent.value: e.result for e in evs if e.type == EventType.RESULT}
    assert len(results) == 6
    assert {o.id: o.cost.total.amount for o in results["decision"].options} == {"OPT-A": 22385, "OPT-B": 2485,
                                                                                   "OPT-C": 33485}
    assert evs[-1].message == "finished"


def test_run_budget_is_enforced(cfg, mock_tools):
    from llm.providers.scripted import ScriptedProvider
    slow_cfg = with_overrides(cfg, runtime={"max_run_seconds": 0.2})
    rt, provider = make_runtime(slow_cfg, mock_tools, scripts.story)
    provider.delay_s = 0.05
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(collect(rt, make_incident()))
    assert isinstance(provider, ScriptedProvider)


def test_bad_tool_args_go_back_to_the_model(cfg, mock_tools):
    from llm.providers.scripted import Turn

    def script(req):
        if req.agent == "monitor" and not any(m["role"] == "tool" for m in req.messages):
            return Turn(text="Checking.", tool_calls=[("get_asset_health", {"asset": "EQ-3012"}),
                                                       ("no_such_tool", {})])
        return Turn(text="ok")

    rt, provider = make_runtime(cfg, mock_tools, script)
    evs = asyncio.run(collect(rt, make_incident()))
    tool_msgs = [m["content"] for r in provider.requests if r.agent == "monitor" for m in r.messages if m["role"] == "tool"]
    assert any("invalid arguments" in m for m in tool_msgs) and any("unknown tool" in m for m in tool_msgs)
    assert any(e.type == EventType.RESULT and e.agent.value == "monitor" for e in evs)  # still completes
