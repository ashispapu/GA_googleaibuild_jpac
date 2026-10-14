"""End-to-end: the ml_alert flow against the mock API with a scripted tool-calling model.
Checks the C3 event contract and the C9 golden numbers."""
import asyncio
from collections import Counter

from plantguard_contracts.schemas.incidents import (AgentEvent, Diagnosis, EnergyResult, EventType, MonitorResult,
                                                    OptionsResponse, SchedulerResult, SparePartsResult)
from plantguard_contracts.tools import TOOLS

from support import collect, make_incident, make_runtime
import scripts

PIPELINE = ["monitor", "diagnosis", "spare_parts", "scheduler", "energy", "decision"]


def run_story(cfg, mock_tools, script=scripts.story, **kw):
    rt, provider = make_runtime(cfg, mock_tools, script, **kw)
    evs = asyncio.run(collect(rt, make_incident()))
    return evs, rt, provider


def test_event_contract(cfg, mock_tools):
    evs, rt, _ = run_story(cfg, mock_tools)
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    for e in evs:  # every frame is a valid contract AgentEvent (round trip through JSON like the WS does)
        AgentEvent.model_validate_json(e.model_dump_json())
    assert (evs[0].agent.value, evs[0].type, evs[0].message) == ("orchestrator", EventType.STATUS, "started")
    assert (evs[-1].agent.value, evs[-1].type, evs[-1].message) == ("orchestrator", EventType.STATUS, "finished")
    started = [e.agent.value for e in evs if e.type == EventType.STATUS and e.message == "started" and not e.sub_agent]
    assert started == ["orchestrator", *PIPELINE]  # C3 agent order
    results = Counter(e.agent.value for e in evs if e.type == EventType.RESULT)
    assert results == Counter({a: 1 for a in PIPELINE})  # exactly one RESULT per agent
    types = {e.agent.value: type(e.result) for e in evs if e.type == EventType.RESULT}
    assert types == {"monitor": MonitorResult, "diagnosis": Diagnosis, "spare_parts": SparePartsResult,
                     "scheduler": SchedulerResult, "energy": EnergyResult, "decision": OptionsResponse}
    for e in evs:
        if e.type == EventType.THOUGHT:
            assert len(e.message) <= 200 and "{" not in e.message
        if e.type in (EventType.TOOL_CALL, EventType.TOOL_RESULT):
            spec = TOOLS[e.tool]
            assert not spec.writes and e.agent.value in spec.agents  # allow-list (C4) and no write tools (C3)


def test_golden_numbers(cfg, mock_tools):
    evs, _, _ = run_story(cfg, mock_tools)
    r = {e.agent.value: e.result for e in evs if e.type == EventType.RESULT}
    assert r["monitor"].severity.value == "high" and r["monitor"].risk_72h == 0.78 and r["monitor"].rul_hours == 70
    d = r["diagnosis"]
    assert d.failure_mode == "bearing_wear" and d.confidence == 0.6 and d.required_parts == ["SKF-6205"]
    assert d.root_cause == "Rear spindle bearing (SKF-6205) wear"
    assert {e.kind.value for e in d.evidence} >= {"ml", "manual", "work_order", "quality", "sensor"}
    sp = r["spare_parts"]
    assert sp.local_stock == 0 and [s.eta_hours for s in sp.sources] == [6, 20, 504]
    assert sp.sources[sp.recommended_source].cost.amount == 435
    w = r["scheduler"].windows[0]
    assert w.start.isoformat() == "2026-10-14T22:00:00+00:00" and w.technician_ids == ["TECH-104"]
    assert (w.end - w.start).total_seconds() == 2.5 * 3600 and w.line_speed_before_pct == 70 and w.deadline_met
    en = {x.option_id: x for x in r["energy"].estimates}
    assert en["OPT-B"].energy_kwh == -140 and en["OPT-B"].co2_kg == -58.8 and r["energy"].excess_power_pct == 8
    opts = {o.id: o for o in r["decision"].options}
    assert {k: o.cost.total.amount for k, o in opts.items()} == {"OPT-A": 22385, "OPT-B": 2485, "OPT-C": 33485}
    assert [o.id for o in opts.values() if o.recommended] == ["OPT-B"]
    assert opts["OPT-B"].deadline_met and not opts["OPT-A"].deadline_met and not opts["OPT-C"].deadline_met
    assert opts["OPT-B"].failure_risk_until_repair == 0.12 and opts["OPT-B"].confidence == 0.6
    assert opts["OPT-B"].label == "Run at 70%, repair Wednesday night"
    assert r["decision"].needs_human_review
    assert r["decision"].reason == "Diagnosis confidence 0.60 is below 0.70 and the plan creates work orders."
    wo = next(a for a in opts["OPT-B"].actions if a.kind == "work_order").draft
    assert wo.technician_id == "TECH-104" and wo.duration_hours == 2.5 and wo.title == "Replace rear spindle bearing EQ-3012"
    assert wo.planned_start.isoformat() == "2026-10-14T22:00:00+00:00" and wo.incident_id == "INC-0042"


def test_same_numbers_as_the_recorded_fixture(cfg, mock_tools):
    """The live run reproduces the recorded run's decision numbers exactly (C9 'same numbers every time')."""
    import json
    from pathlib import Path
    rec = [json.loads(l) for l in (Path(__file__).resolve().parents[2] / "fixtures/story/agent_events_INC-0042.jsonl")
           .read_text().splitlines()]
    recorded = next(e["result"] for e in rec if e["type"] == "result" and e["agent"] == "decision")
    evs, _, _ = run_story(cfg, mock_tools)
    live = next(e.result for e in evs if e.type == EventType.RESULT and e.agent.value == "decision")
    for a, b in zip(recorded["options"], live.model_dump(mode="json")["options"]):
        assert a["id"] == b["id"] and a["cost"] == b["cost"] and a["deadline_met"] == b["deadline_met"]
        assert (a["failure_risk_until_repair"], a["confidence"], a["recommended"]) == \
               (b["failure_risk_until_repair"], b["confidence"], b["recommended"])
        assert [x["kind"] for x in a["actions"]] == [x["kind"] for x in b["actions"]]
        if a["id"] != "OPT-C":
            assert (a["energy_kwh"], a["co2_kg"]) == (b["energy_kwh"], b["co2_kg"])
    # The recording is internally inconsistent for OPT-C: its Energy RESULT says +60 kWh / +25.2 kg but its option
    # shows 0. The live run takes the option's energy from the Energy agent, so it shows +60 / +25.2 consistently.
    rec_energy = next(e["result"] for e in rec if e["type"] == "result" and e["agent"] == "energy")
    c_est = next(x for x in rec_energy["estimates"] if x["option_id"] == "OPT-C")
    c_live = next(o for o in live.options if o.id == "OPT-C")
    assert (c_live.energy_kwh, c_live.co2_kg) == (c_est["energy_kwh"], c_est["co2_kg"])


def test_streaming_deltas_rebuild_the_closing_event(cfg, mock_tools):
    evs, _, _ = run_story(cfg, mock_tools, piece_chars=3)
    deltas = [e for e in evs if e.type in (EventType.THOUGHT_DELTA, EventType.MESSAGE_DELTA)]
    assert deltas, "thinking and answers must stream"
    assert any(e.type == EventType.THOUGHT_DELTA for e in deltas) and any(e.type == EventType.MESSAGE_DELTA for e in deltas)
    by_stream: dict[str, str] = {}
    for e in deltas:
        by_stream[e.stream_id] = by_stream.get(e.stream_id, "") + e.message
    closers = {e.stream_id: e for e in evs if e.stream_id and e.type in (EventType.THOUGHT, EventType.MESSAGE)}
    assert set(by_stream) == set(closers)  # every stream is closed by exactly one regular event
    for sid, text in by_stream.items():
        closer = closers[sid]
        assert closer.seq > max(e.seq for e in deltas if e.stream_id == sid)
        if closer.type == EventType.MESSAGE:
            assert closer.message == text.strip()
        else:
            assert len(closer.message) <= 200
    summary = [e for e in evs if e.agent.value == "orchestrator" and e.type == EventType.MESSAGE]
    assert summary and "€2,485" in summary[-1].message  # the Ask panel answer streams too


def test_sub_agent_reports_back_under_its_principal(cfg, mock_tools):
    evs, rt, _ = run_story(cfg, mock_tools)
    helper = [e for e in evs if e.sub_agent == "manual_researcher"]
    assert helper and all(e.agent.value == "diagnosis" for e in helper)
    assert helper[0].type == EventType.STATUS and helper[0].message == "started"
    assert any(e.type == EventType.STATUS and e.message == "finished" for e in helper)
    assert any(e.type == EventType.TOOL_CALL and e.tool == "search_manuals" for e in helper)
    assert not any(e.type == EventType.RESULT for e in helper)  # helpers report back; they have no RESULT
    # the diagnosis did not call search_manuals itself, yet its result cites the manual the helper found
    diag = next(e.result for e in evs if e.type == EventType.RESULT and e.agent.value == "diagnosis")
    assert "MAN-SPINDLE-200#7.4" in {x.ref_id for x in diag.evidence}


def test_llm_wording_is_used_but_numbers_are_not(cfg, mock_tools):
    evs, _, _ = run_story(cfg, mock_tools)
    d = next(e.result for e in evs if e.type == EventType.RESULT and e.agent.value == "diagnosis")
    assert d.alternatives == ["Spindle misalignment (manual §7.6) — kurtosis argues against it"]
    assert "NOT-IN-BOM" not in d.required_parts  # parts are checked against the BOM


def test_redaction_never_leaks_names_and_restores_for_the_ui(cfg, mock_tools):
    reports = []
    evs, rt, provider = run_story(cfg, mock_tools, sink=reports.append)
    sent = " ".join((r.system + " " + " ".join(str(m.get("content")) for m in r.messages)) for r in provider.requests)
    for name in ("Suresh", "Meera", "Arjun"):
        assert name not in sent
    assert "<PERSON_NAME_" in sent
    assert reports and len(reports) == len(provider.requests)  # one RedactionReport per LLM call (C7)
    assert any(f.kind == "person_name" for r in reports for f in r.findings)
    sched = " ".join(e.message for e in evs if e.agent.value == "scheduler")
    assert "<PERSON_NAME_" not in sched and ("Suresh" in sched or "Meera" in sched)


def test_lazy_model_still_produces_complete_results(cfg, mock_tools):
    """The model never calls a tool: required data is fetched by code, results are identical."""
    evs, _, _ = run_story(cfg, mock_tools, script=scripts.lazy)
    r = {e.agent.value: e.result for e in evs if e.type == EventType.RESULT}
    assert set(r) == set(PIPELINE)
    assert {o.id: o.cost.total.amount for o in r["decision"].options} == {"OPT-A": 22385, "OPT-B": 2485, "OPT-C": 33485}
    assert r["diagnosis"].confidence == 0.6
