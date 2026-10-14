"""The live agent runtime inside P1's real backend: WS stream -> stored events -> options -> approval -> execution.
The model is scripted (selected through the .ini, exactly like a real provider), everything else is production code."""
import sys
from pathlib import Path

import pytest

from plantguard_contracts.schemas.incidents import AgentEvent, EventType

ROOT = Path(__file__).resolve().parents[2]
CENTRAL = ROOT / "p2-agents" / "config" / "agents.ini"
sys.path.insert(0, str(ROOT / "p1-backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.container import build_container  # noqa: E402
from app.main import create_app  # noqa: E402


def backend(tmp_path, monkeypatch, provider: str):
    ini = tmp_path / "agents.ini"
    ini.write_text(CENTRAL.read_text().replace("provider = litellm", f"provider = {provider}", 1))
    monkeypatch.setenv("PLANTGUARD_AGENTS_INI", str(ini))
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    from app.core.config import Settings
    c = build_container(Settings.from_env())
    return TestClient(create_app(c)), c


def token(client, user):
    r = client.post("/auth/token", json={"username": user, "password": "demo"})
    return r.json()["access_token"]


def stream(client, inc_id, tok):
    evs = []
    with client.websocket_connect(f"/incidents/{inc_id}/stream?token={tok}&speed=50") as ws:
        try:
            while True:
                evs.append(AgentEvent.model_validate_json(ws.receive_text()))
        except Exception:
            pass
    return evs


def test_live_story_through_backend(tmp_path, monkeypatch):
    client, c = backend(tmp_path, monkeypatch, "scripts:StoryProvider")
    op, lead = token(client, "op"), token(client, "lead")
    H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    inc = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=H(op)).json()
    evs = stream(client, inc["id"], op)
    assert evs[-1].agent.value == "orchestrator" and evs[-1].message == "finished"
    assert not any(e.type == EventType.ERROR for e in evs), "live run must not fall back to the replay"
    assert any(e.type == EventType.THOUGHT_DELTA for e in evs) and any(e.sub_agent for e in evs)
    # REST catch-up returns exactly what the socket streamed (incl. deltas), in seq order
    stored = client.get(f"/incidents/{inc['id']}/events", headers=H(op)).json()
    assert [e["seq"] for e in stored] == [e.seq for e in evs] == list(range(1, len(evs) + 1))
    # incident updated from the results
    got = client.get(f"/incidents/{inc['id']}", headers=H(op)).json()
    assert got["status"] == "awaiting_approval" and got["diagnosis"]["confidence"] == 0.6
    opts = client.get(f"/incidents/{inc['id']}/options", headers=H(op)).json()
    assert {o["id"]: o["cost"]["total"]["amount"] for o in opts["options"]} == {"OPT-A": 22385, "OPT-B": 2485,
                                                                               "OPT-C": 33485}
    # tool calls went through the gateway: audited, allow-listed
    page, _, _ = c.audit.list(incident_id=inc["id"], limit=500)
    calls = [e for e in page if e.action == "tool_call"]
    assert len(calls) == 14 and {e.actor for e in calls} <= {"monitor", "diagnosis", "spare_parts", "scheduler",
                                                            "energy", "decision"}
    assert c.audit.verify()[0]
    # every LLM call left a redaction report for the Security page
    reds = client.get("/security/redactions", headers=H(op)).json()
    assert any(r["agent"] == "scheduler" for r in reds)
    # a lead approves B -> backend executes the drafted actions (agents never write)
    appr = client.post(f"/incidents/{inc['id']}/approve", headers=H(lead),
                       json={"option_id": "OPT-B", "decision": "approve", "reason": "matches 2024 failure"}).json()
    assert len(appr["executed_actions"]) == 3 and all(a["ok"] for a in appr["executed_actions"])
    fb = client.post(f"/incidents/{inc['id']}/feedback", headers=H(op),
                     json={"technician_id": "TECH-104", "confirmed": True, "actual_root_cause": "outer race worn"}).json()
    assert fb["new_similar_case_confidence"] == 0.82


def test_follow_up_question_opens_a_thread_and_is_handed_off(tmp_path, monkeypatch):
    client, _ = backend(tmp_path, monkeypatch, "scripts:QuestionProvider")
    op = token(client, "op")
    H = {"Authorization": f"Bearer {op}"}
    alert = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=H).json()
    q = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "user_question",
                                        "question": "Is there a spare bearing?"}, headers=H).json()
    assert q["id"] != alert["id"] and q["question"] == "Is there a spare bearing?"
    # the alert is still the active incident for the asset
    again = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=H).json()
    assert again["id"] == alert["id"]
    evs = stream(client, q["id"], op)
    assert any(e.type == EventType.HANDOFF and e.args["to"] == "spare_parts" for e in evs)
    answers = [e for e in evs if e.agent.value == "spare_parts" and e.type == EventType.MESSAGE]
    assert answers and "Plant B" in answers[-1].message
    assert evs[-1].message == "finished"


def test_llm_failure_falls_back_to_the_recording(tmp_path, monkeypatch):
    client, _ = backend(tmp_path, monkeypatch, "scripts:FailingProvider")
    op = token(client, "op")
    inc = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"},
                      headers={"Authorization": f"Bearer {op}"}).json()
    evs = stream(client, inc["id"], op)
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    err = next(e for e in evs if e.type == EventType.ERROR)
    assert "LLM_UNAVAILABLE" in err.message
    assert evs[-1].message == "finished"
    opts = client.get(f"/incidents/{inc['id']}/options", headers={"Authorization": f"Bearer {op}"}).json()
    assert next(o for o in opts["options"] if o["recommended"])["id"] == "OPT-B"


def test_no_api_key_keeps_the_replay(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client, _ = backend(tmp_path, monkeypatch, "litellm")
    op = token(client, "op")
    inc = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"},
                      headers={"Authorization": f"Bearer {op}"}).json()
    evs = stream(client, inc["id"], op)
    assert len(evs) == 34 and not any(e.type == EventType.ERROR for e in evs)  # the recording, untouched
