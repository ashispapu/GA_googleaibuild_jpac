"""Real reset API, isolated SQLite plant, no provider requests and no changes to the running local stack."""
import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.container import build_container
from app.main import create_app
from app.repositories.incident_repository import IncidentRecord
from app.services.run_coordinator import RunCoordinator, RunReset
from plantguard_contracts.schemas.common import Health, MachineState
from plantguard_contracts.schemas.incidents import Agent, AgentEvent, EventType, Incident, IncidentStatus, IncidentTrigger, Severity
from plantguard_contracts.schemas.ml import AssetHealth
from plantguard_contracts.schemas.ot import TelemetryMessage

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def plant(tmp_path, monkeypatch):
    ini = (ROOT / "config/simulation.ini").read_text().replace("data_dir = var/plantguard", f"data_dir = {tmp_path}")
    ini = ini.replace("random = true", "random = false")
    path = tmp_path / "simulation.ini"
    path.write_text(ini)
    settings = {"DATA_MODE": "sim", "AUTH_MODE": "local", "PLANTGUARD_ROLE": "all",
                "PLANTGUARD_ENV": "dev", "PLANTGUARD_SIM_INI": str(path), "SIM_RESET": "",
                "GEMINI_API_KEY": "", "GOOGLE_GENAI_USE_ENTERPRISE": "false", "GOOGLE_GENAI_USE_VERTEXAI": "false",
                "QDRANT_URL": "", "REDIS_HOST": "", "REDIS_URL": "", "DB_HOST": "",
                "PLANTGUARD_OT_DATABASE_URL": "", "PLANTGUARD_IT_DATABASE_URL": ""}
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    c = build_container()
    c.background = []  # unit tests drive ticks explicitly; no live agent/model calls or timing races
    with TestClient(create_app(c)) as client:
        def auth(user):
            r = client.post("/auth/token", json={"username": user, "password": "demo"})
            assert r.status_code == 200
            return {"Authorization": "Bearer " + r.json()["access_token"]}
        yield c, client, auth


def incident(c, asset="EQ-3012"):
    a = c.engine.assets[asset]
    now = c.clock()
    inc = Incident(id=c.incidents.repo.next_id(), title="Test issue", asset_id=asset, plant_id=a.plant_id,
                   line_id=a.line_id, trigger=IncidentTrigger.ML_ALERT, severity=Severity.HIGH,
                   status=IncidentStatus.ANALYSING, created_at=now, updated_at=now)
    rec = IncidentRecord(inc)
    c.incidents.repo.add(rec)
    return rec


def test_reset_restores_all_visible_machines_and_preserves_identity_and_history(plant):
    c, client, auth = plant
    headers = auth("lead")
    a = c.engine.assets["EQ-3012"]
    rec = incident(c)
    c.engine.inject_fault(a.asset_id, "bearing_wear", progress=1)
    c.engine.set_state(a.asset_id, "fault", "breakdown")
    c.engine.ot.x("INSERT INTO alarms(id, asset_id, code, severity, message, raised_at) VALUES(?,?,?,?,?,?)",
                  ("ALM-RESET", a.asset_id, "MCH-FLT", "critical", "Fault", c.clock().isoformat()))
    c.scores.put(AssetHealth(asset_id=a.asset_id, evaluated_at=c.clock(), health=Health.CRITICAL, risk_72h=.99,
                            rul_hours=1, anomaly_score=1, rule_threshold_breached=True, top_features=[], model_version="test"))
    c.engine.it.x("INSERT INTO line_speed_plan(plant_id,line_id,speed_pct,start_ts,created_at) VALUES(?,?,?,?,?)",
                  ("PLANT-A", a.line_id, 0, c.clock().isoformat(), c.clock().isoformat()))
    # Another plant is deliberately not affected by a lead's reset.
    other = next(x for x in c.engine.assets.values() if x.plant_id == "PLANT-B")
    c.engine.inject_fault(other.asset_id, "bearing_wear", progress=1)
    c.engine.set_state(other.asset_id, "fault", "breakdown")
    users = [tuple(r) for r in c.engine.it.q("SELECT * FROM users ORDER BY id")]
    docs = c.engine.it.val("SELECT COUNT(*) FROM documents")
    response = client.post("/simulator/reset", headers=headers, json={"plant_id": "PLANT-A"})
    assert response.status_code == 200, response.text
    assert response.json()["random_faults"] is False
    assert c.incidents.repo.get(rec.incident.id).incident.status == IncidentStatus.RESOLVED
    assert c.engine.condition(a.asset_id) is None
    assert c.engine.machine_state(a.asset_id)[0] == "run"
    assert c.engine.line_speed("PLANT-A", a.line_id) == 100
    assert c.engine.ot.val("SELECT COUNT(*) FROM alarms WHERE asset_id=? AND cleared_at IS NULL", (a.asset_id,)) == 0
    machines = [asset for p in client.get("/plants", headers=headers).json() for line in p["lines"]
                for station in line["stations"] for asset in station["assets"]]
    assert all(m["health"] == "ok" and m["state"] == "run" for m in machines)
    assert client.get(f"/ml/assets/{a.asset_id}/health", headers=headers).json()["health"] == "ok"
    assert client.get("/incidents?scope=open", headers=headers).json()["items"] == []
    assert client.get("/garden", headers=headers).json()["agents"] == []
    assert users == [tuple(r) for r in c.engine.it.q("SELECT * FROM users ORDER BY id")]
    assert docs == c.engine.it.val("SELECT COUNT(*) FROM documents")
    assert c.engine.condition(other.asset_id) is not None and c.engine.machine_state(other.asset_id)[0] == "fault"
    assert c.engine.it.val("SELECT COUNT(*) FROM audit_log WHERE action='reset_plant'") == 1
    assert c.engine.it.meta("story_faults_pending") == "1"
    # Repeating the action is safe; it does not delete the saved incident or recreate old problems.
    assert client.post("/simulator/reset", headers=headers, json={"plant_id": "PLANT-A"}).status_code == 200
    assert c.incidents.repo.get(rec.incident.id).incident.status == IncidentStatus.RESOLVED


def test_reset_requires_role_and_plant_scope(plant):
    c, client, auth = plant
    body = {"plant_id": "PLANT-A"}
    assert client.post("/simulator/reset", json=body).status_code == 401
    assert client.post("/simulator/reset", headers=auth("op"), json=body).status_code == 403
    assert client.post("/simulator/reset", headers=auth("leadb"), json=body).status_code == 403
    assert c.engine.it.meta("sim_reset_generation:PLANT-A") is None


def test_old_telemetry_and_alarms_are_ignored_after_reset(plant):
    c, client, auth = plant
    aid = "EQ-3012"
    before = c.clock()
    assert client.post("/simulator/reset", headers=auth("lead"), json={"plant_id": "PLANT-A"}).status_code == 200
    assert c.engine.event_before_reset(aid, before)
    assert not c.engine.event_before_reset(aid, c.clock() + timedelta(seconds=1))
    scorer = c.plant_reset.scorer
    msg = TelemetryMessage(asset_id=aid, line_id=c.engine.assets[aid].line_id, ts=before,
                           state=MachineState.RUN, values={"vibration_rms": 100})
    scorer.ingest(msg)
    assert not scorer._buffers.get(aid)
    async def alarm():
        await c.workflow.on_alarm({"asset_id": aid, "severity": "critical", "ts": before.isoformat()})
    asyncio.run(alarm())
    assert c.incidents.repo.all() == []


def test_old_worker_is_cancelled_even_while_waiting_for_model(plant):
    c, _, _ = plant
    rec = incident(c)
    async def scenario():
        started = asyncio.Event()
        class WaitingRuntime:
            async def run(self, inc):
                started.set()
                await asyncio.Event().wait()
                yield  # never reached
        runs = RunCoordinator(c.incidents.repo, c.bus, lambda _: WaitingRuntime(), c.audit, c.clock)
        task = asyncio.create_task(runs._run(rec.incident.id, 1))
        await started.wait()
        c.engine.it.set_meta("sim_reset_generation:PLANT-A", "new-generation")
        await asyncio.wait_for(task, 2)
        assert c.incidents.repo.get(rec.incident.id).events == []
        rec.reset_generation = "old-generation"
        event = AgentEvent(incident_id=rec.incident.id, seq=1, ts=c.clock(), agent=Agent.ORCHESTRATOR,
                           type=EventType.MESSAGE, message="Old answer")
        with pytest.raises(RunReset):
            await runs._record(rec, event)
    asyncio.run(scenario())


def test_api_requests_reset_from_separate_simulation_leader(plant):
    c, _, _ = plant
    async def scenario():
        c.engine.drives_plant = False
        c.engine.inject_fault("EQ-3012", "bearing_wear", progress=.9)
        request = asyncio.create_task(c.plant_reset.request("PLANT-A", "USR-lead", timeout_s=2))
        await asyncio.sleep(.05)
        assert c.engine.it.meta("sim_reset_request") != "null"
        # The leader's tick (also when the plant clock is paused) applies the request and acknowledges it.
        c.clock.set_scale(0)
        await c.engine.tick()
        await request
        assert c.engine.condition("EQ-3012") is None
        assert c.clock.scale == c.engine.cfg.scale
        assert c.engine.it.meta("sim_reset_request") == "null"
        c.engine.drives_plant = True
    asyncio.run(scenario())
