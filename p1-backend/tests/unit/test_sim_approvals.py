"""Simulated plant (v1.2): approval chain, purchase orders and the options chat, end to end through the API.

One plant is seeded into a temp folder and driven by the real background tasks (engine, scoring, rule-based agents),
exactly as `make run-p1` would, so these tests also prove the pieces are wired together."""
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
INC = "INC-0042"


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("plant")
    ini = (ROOT / "config" / "simulation.ini").read_text()
    ini = ini.replace("data_dir = var/plantguard", f"data_dir = {tmp}").replace("scale = 60", "scale = 120")
    ini = ini.replace("mean_interval_h = 10", "mean_interval_h = 100000")  # no random faults during the test
    (tmp / "simulation.ini").write_text(ini)
    old = {k: os.environ.get(k) for k in ("DATA_MODE", "PLANTGUARD_SIM_INI", "SIM_RESET", "GEMINI_API_KEY")}
    os.environ.update(DATA_MODE="sim", PLANTGUARD_SIM_INI=str(tmp / "simulation.ini"), SIM_RESET="1", GEMINI_API_KEY="")
    sys.path.insert(0, str(ROOT / "p2-agents"))
    from app.container import build_container
    from app.main import create_app
    try:
        with TestClient(create_app(build_container())) as client:
            yield client
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def tok(client, user):
    r = client.post("/auth/token", json={"username": user, "password": "demo"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def wait_stage(client, h, stage, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/incidents/{INC}/overview", headers=h)
        if r.status_code == 200 and r.json()["stage"] == stage:
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"{INC} never reached {stage}")


def test_options_chat_chain_and_purchase_order(sim):
    lead, leadb, manager = tok(sim, "lead"), tok(sim, "leadb"), tok(sim, "manager")
    wait_stage(sim, lead, "awaiting_approval")
    opts = {o["id"]: o for o in sim.get(f"/incidents/{INC}/options", headers=lead).json()["options"]}
    signers = {k: [s["key"] for s in o["approvals_needed"]] for k, o in opts.items()}
    # the AI's plan takes Plant B's spare: Plant B must release it; the expensive ones need the manager
    assert signers["OPT-B"] == ["requesting_plant", "source_plant:PLANT-B"]
    assert "manager" in signers["OPT-C"] and "purchase_order" in [a["kind"] for a in opts["OPT-C"]["actions"]]

    # ---- options chat: a suggestion becomes a new costed option; the AI's options stay as they were ----
    r = sim.post(f"/incidents/{INC}/options/chat", headers=lead, json={"message": "Repair tomorrow morning at 80% speed"})
    assert r.status_code == 200, r.text
    new = r.json()["added_option_id"]
    assert new == "OPT-D" and "option D" in r.json()["reply"]["text"]
    after = {o["id"]: o for o in r.json()["options"]["options"]}
    assert after["OPT-D"]["origin"] == "approver" and not after["OPT-D"]["recommended"]
    assert {k: after[k]["cost"] for k in opts} == {k: opts[k]["cost"] for k in opts}
    assert after["OPT-B"]["recommended"]
    again = sim.post(f"/incidents/{INC}/options/chat", headers=lead, json={"message": "Repair tomorrow morning at 80% speed"})
    assert again.json()["added_option_id"] is None  # same plan is not added twice
    assert sim.post(f"/incidents/{INC}/options/chat", headers=tok(sim, "op"), json={"message": "night"}).status_code == 403
    assert len(sim.get(f"/incidents/{INC}/options/chat", headers=lead).json()) == 4

    # ---- approval chain: the lead's signature alone creates nothing ----
    a = sim.post(f"/incidents/{INC}/approve", headers=lead, json={"option_id": "OPT-B", "decision": "approve", "reason": "night swap"})
    assert a.status_code == 200 and a.json()["approval_token"] is None and a.json()["executed_actions"] == []
    assert wait_stage(sim, lead, "awaiting_signoff")
    assert sim.post(f"/incidents/{INC}/options/chat", headers=lead, json={"message": "night"}).status_code == 409
    tasks = sim.get("/incidents/signoffs", headers=leadb).json()
    assert [(t["incident_id"], t["step"]["key"]) for t in tasks] == [(INC, "source_plant:PLANT-B")]
    assert sim.get(f"/incidents/{INC}", headers=leadb).status_code == 200  # may read what it is asked to release
    # Plant B declines: back to the lead, nothing created
    d = sim.post(f"/incidents/{INC}/approve", headers=leadb, json={"option_id": "OPT-B", "decision": "reject",
                                                                    "reason": "we need it for EQ-4002"})
    assert d.status_code == 200
    inc = sim.get(f"/incidents/{INC}", headers=lead).json()
    assert inc["status"] == "awaiting_approval" and inc["signoff"]["status"] == "declined"

    # ---- buy from the supplier instead: the manager must co-sign, then a purchase order is placed ----
    assert sim.post(f"/incidents/{INC}/approve", headers=lead,
                    json={"option_id": "OPT-C", "decision": "approve", "reason": "buy one"}).json()["approval_token"] is None
    assert sim.post(f"/incidents/{INC}/approve", headers=leadb,
                    json={"option_id": "OPT-C", "decision": "approve", "reason": "x"}).status_code == 403
    final = sim.post(f"/incidents/{INC}/approve", headers=manager, json={"option_id": "OPT-C", "decision": "approve",
                                                                         "reason": "ok"}).json()
    assert final["approval_token"] and {x["kind"] for x in final["executed_actions"]} >= {"purchase_order"}
    assert [s["signed_by"] for s in final["signoffs"]] == ["USR-lead", "USR-manager"]
    job = sim.get(f"/incidents/{INC}/job", headers=lead).json()
    assert job["parts"][0]["purchase_order_id"].startswith("PO-") and "purchase order" in job["blocked_reason"]
    notes = [u["note"] for u in sim.get(f"/incidents/{INC}/timeline", headers=lead).json()]
    assert any("Declined option B" in n for n in notes) and any("co-signed by Karan" in n for n in notes)
