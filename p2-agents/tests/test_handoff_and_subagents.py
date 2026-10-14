"""Divert (hand-off) vs sub-agent, in the user_question flow."""
import asyncio

from plantguard_contracts.schemas.incidents import EventType

import scripts
from support import collect, make_incident, make_runtime, with_overrides

Q = "Is there a spare bearing for EQ-3012?"


def test_handoff_diverts_the_task_and_the_target_answers(cfg, mock_tools):
    rt, provider = make_runtime(cfg, mock_tools, scripts.question_handoff)
    evs = asyncio.run(collect(rt, make_incident("user_question", Q)))
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    h = [e for e in evs if e.type == EventType.HANDOFF]
    assert len(h) == 1 and h[0].agent.value == "orchestrator"
    assert h[0].args == {"to": "spare_parts", "task": "Is a spare SKF-6205 bearing available?", "reason": "stock question"}
    # the diverting agent says the task is now elsewhere, and does nothing more
    after = evs[evs.index(h[0]) + 1]
    assert after.agent.value == "orchestrator" and after.type == EventType.MESSAGE and "now with spare parts" in after.message
    assert sum(1 for r in provider.requests if r.agent == "orchestrator") == 1
    # the target runs next in answer mode: STATUS started -> tool -> streamed MESSAGE -> STATUS finished, no RESULT
    sp = [e for e in evs if e.agent.value == "spare_parts"]
    assert sp[0].type == EventType.STATUS and sp[0].message == "started"
    assert any(e.type == EventType.TOOL_CALL and e.tool == "check_stock" for e in sp)
    answer = [e for e in sp if e.type == EventType.MESSAGE]
    assert answer and "Plant B has 4" in answer[-1].message
    assert any(e.type == EventType.MESSAGE_DELTA for e in sp)
    assert sp[-1].type == EventType.STATUS and sp[-1].message == "finished"
    assert not any(e.type == EventType.RESULT for e in evs)
    assert (evs[-1].agent.value, evs[-1].message) == ("orchestrator", "finished")
    # the hand-off task text reached the target model
    sp_req = next(r for r in provider.requests if r.agent == "spare_parts")
    assert "Is a spare SKF-6205 bearing available?" in sp_req.messages[0]["content"]


def test_sub_agent_reports_back_and_caller_answers(cfg, mock_tools):
    rt, provider = make_runtime(cfg, mock_tools, scripts.question_subagent)
    evs = asyncio.run(collect(rt, make_incident("user_question", "Why is EQ-3012 red?")))
    mon = [e for e in evs if e.agent.value == "monitor"]
    assert mon[0].message == "started" and mon[-1].message == "finished"
    assert not any(e.type == EventType.RESULT for e in mon)  # sub mode: answer goes back, no RESULT
    final = [e for e in evs if e.agent.value == "orchestrator" and e.type == EventType.MESSAGE]
    assert final and final[-1].message.startswith("The monitor says: Failure risk is 78%")
    assert not any(e.type == EventType.HANDOFF for e in evs)
    # the caller had the ask_monitor tool but no handoff in plan mode, and the sub-agent had no handoff (sub mode)
    orch_tools = {t["function"]["name"] for t in provider.requests[0].tools}
    assert {"ask_monitor", "ask_diagnosis", "handoff_task"} <= orch_tools
    mon_tools = {t["function"]["name"] for t in next(r for r in provider.requests if r.agent == "monitor").tools}
    assert "handoff_task" not in mon_tools and "get_asset_health" in mon_tools


def test_handoff_limit_is_enforced(cfg, mock_tools):
    cfg0 = with_overrides(cfg, runtime={"max_handoffs": 0})
    rt, provider = make_runtime(cfg0, mock_tools, scripts.question_handoff)
    evs = asyncio.run(collect(rt, make_incident("user_question", Q)))
    assert not any(e.type == EventType.HANDOFF for e in evs)
    tool_msgs = [m for r in provider.requests for m in r.messages if m["role"] == "tool"]
    assert any("hand-off limit" in m["content"] for m in tool_msgs)


def test_plan_mode_has_no_team_tools(cfg, mock_tools):
    rt, provider = make_runtime(cfg, mock_tools, scripts.lazy)
    asyncio.run(collect(rt, make_incident()))
    plan = provider.requests[0]
    assert plan.agent == "orchestrator" and plan.tools == []
