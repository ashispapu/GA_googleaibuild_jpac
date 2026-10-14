"""Contract tests.
  make test        -> fixtures + API tests against the mock server
  make test-p1     -> the same API tests against P1's real backend (p1-backend/app/main.py)"""
import importlib, json, os, sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from plantguard_contracts.schemas import (approvals, energy, engineering, erp, incidents, maintenance, mes, ml, ot,
                                          plants, quality, workforce)
from plantguard_contracts.schemas.ml import FEATURES
from plantguard_contracts.tools import TOOLS, openai_tool_schema

FX = Path(__file__).parent.parent / "fixtures" / "story"

SCHEMA_OF = {
    "plants": list[plants.Plant], "signals_EQ-3012": list[ot.SignalSeries], "alarms": list[ot.Alarm],
    "part_SKF-6205": erp.Part, "supplier_SUP-17": erp.Supplier, "orders": list[mes.ProductionOrder],
    "schedule_L3": list[mes.ScheduleSlot], "oee_L3": mes.LineOEE, "history_EQ-3012": list[maintenance.WorkOrder],
    "defects_L3": quality.DefectSummary, "bom_EQ-3012": engineering.AssetBom,
    "docsearch_bearing": engineering.DocSearchResponse, "technicians": list[workforce.Technician],
    "shifts_PLANT-A": list[workforce.Shift], "energy_L3": energy.LineConsumption, "tariffs": list[energy.Tariff],
    "co2_factor": energy.Co2Factor, "health_EQ-3012": ml.AssetHealth, "models": list[ml.ModelInfo],
    "options_INC-0042": incidents.OptionsResponse, "incident_INC-0042": incidents.Incident,
    "approval_INC-0042": approvals.Approval, "feedback_INC-0042": approvals.Feedback,
}


@pytest.mark.parametrize("name", SCHEMA_OF)
def test_fixture_matches_schema(name):
    TypeAdapter(SCHEMA_OF[name]).validate_json((FX / f"{name}.json").read_text())


def test_agent_events_are_ordered_and_typed():
    evs = [incidents.AgentEvent.model_validate_json(l) for l in (FX / "agent_events_INC-0042.jsonl").read_text().splitlines()]
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    results = {e.agent: type(e.result) for e in evs if e.type == incidents.EventType.RESULT}
    assert results[incidents.Agent.DIAGNOSIS] is incidents.Diagnosis
    assert results[incidents.Agent.DECISION] is incidents.OptionsResponse
    assert results[incidents.Agent.ENERGY] is incidents.EnergyResult
    for e in evs:
        if e.tool:
            assert e.tool in TOOLS and e.agent.value in TOOLS[e.tool].agents, e.tool


def test_story_numbers():
    opts = incidents.OptionsResponse.model_validate_json((FX / "options_INC-0042.json").read_text())
    b = next(o for o in opts.options if o.recommended)
    assert b.id == "OPT-B" and b.deadline_met and round(b.cost.total.amount) == 2485 and b.confidence == 0.6
    assert opts.needs_human_review
    h = ml.AssetHealth.model_validate_json((FX / "health_EQ-3012.json").read_text())
    assert h.risk_72h >= 0.7 and not h.rule_threshold_breached  # ML beats the rule
    assert {f.name for f in h.top_features} <= set(FEATURES)


def test_tools_have_llm_schemas():
    for t in TOOLS.values():
        assert openai_tool_schema(t)["function"]["parameters"]["type"] == "object"


# ---------------- API ----------------
def _target_app():
    target = os.environ.get("PLANTGUARD_APP", "mock_server.app:app")  # e.g. app.main:app for P1
    mod, attr = target.split(":")
    return getattr(importlib.import_module(mod), attr)


c = TestClient(_target_app())


def token(user):
    r = c.post("/auth/token", json={"username": user, "password": "demo"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_auth_required():
    r = c.get("/plants")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


def test_plant_scope():
    r = c.get("/workforce/shifts", params={"plant_id": "PLANT-B"}, headers=token("lead"))
    assert r.status_code == 403 and r.json()["code"] == "PLANT_SCOPE"


def test_unknown_id_is_404_error_shape():
    r = c.get("/erp/parts/XXX-1", headers=token("lead"))
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


def test_full_story():
    op, lead, admin = token("op"), token("lead"), token("admin")
    assert c.post("/simulator/scenario/calm", headers=admin).status_code == 200
    assert c.get("/ml/assets/EQ-3012/health", headers=op).json()["health"] == "ok"
    c.post("/simulator/scenario/warning", headers=admin)
    assert c.get("/ml/assets/EQ-3012/health", headers=op).json()["health"] == "critical"
    inc = c.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=op).json()
    with c.websocket_connect(f"/incidents/{inc['id']}/stream?token={op['Authorization'][7:]}&speed=200") as ws:
        evs = []
        try:
            while True:
                evs.append(incidents.AgentEvent.model_validate_json(ws.receive_text()))
        except Exception:
            pass
    assert evs[-1].message == "finished"
    opts = c.get(f"/incidents/{inc['id']}/options", headers=op).json()
    assert opts["needs_human_review"]
    # operator cannot approve
    r = c.post(f"/incidents/{inc['id']}/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "x"}, headers=op)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    # write without approval token is rejected
    draft = opts["options"][1]["actions"][2]["draft"]
    r = c.post("/maintenance/work-orders", json=draft | {"approval_token": "nope"}, headers=lead)
    assert r.status_code == 403 and r.json()["code"] == "APPROVAL_INVALID"
    # lead approves -> actions executed
    appr = c.post(f"/incidents/{inc['id']}/approve", json={"option_id": "OPT-B", "decision": "approve",
                                                          "reason": "matches 2024 failure"}, headers=lead).json()
    assert len(appr["executed_actions"]) == 3 and all(a["ok"] for a in appr["executed_actions"])
    # approval token works on write endpoints
    r = c.post("/maintenance/work-orders", json=draft | {"approval_token": appr["approval_token"]}, headers=lead)
    assert r.status_code == 201
    # second approval conflicts
    assert c.post(f"/incidents/{inc['id']}/approve", json={"option_id": "OPT-A", "decision": "approve", "reason": "x"},
                  headers=lead).status_code == 409
    fb = c.post(f"/incidents/{inc['id']}/feedback", json={"technician_id": "TECH-104", "confirmed": True,
                                                         "actual_root_cause": "outer race worn"}, headers=op).json()
    assert fb["stored_for_retraining"]
    assert c.get("/audit/verify", headers=admin).json()["valid"]


def test_ot_stream():
    t = token("op")["Authorization"][7:]
    with c.websocket_connect(f"/ot/stream?token={t}&line_id=L3") as ws:
        m = ot.TelemetryMessage.model_validate_json(ws.receive_text())
        assert m.line_id == "L3"


def test_every_read_tool_works_on_api():
    """Each read tool's endpoint + input + output must line up with the API."""
    import asyncio, sys, httpx
    sys.path.insert(0, str(Path(__file__).parent.parent / "p2-agents"))
    from agents.tool_client import HttpToolClient
    import test_tools_on_mock as t
    tc = HttpToolClient("http://api", transport=httpx.ASGITransport(app=c.app))
    for tool, (agent, args) in t.CALLS.items():
        asyncio.run(tc.call(agent, tool, args, "INC-0042"))
    assert set(t.CALLS) == {n for n, s in TOOLS.items() if not s.writes}


def test_warning_scene_opens_incident():
    admin = token("admin")
    c.post("/simulator/scenario/reset", headers=admin)
    assert c.get("/incidents/INC-0042", headers=admin).status_code == 404
    c.post("/simulator/scenario/warning", headers=admin)
    assert c.get("/incidents/INC-0042", headers=admin).json()["status"] == "analysing"
