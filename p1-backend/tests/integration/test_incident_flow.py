from helpers import run_story


def approve(client, users, inc, option="OPT-B", who="lead"):
    """Helper: approve an option as the given user."""
    return client.post(f"/incidents/{inc}/approve", json={"option_id": option, "decision": "approve", "reason": "ok"},
                       headers=users[who])


def test_golden_numbers_after_execution(client, users):
    """After approving OPT-B the ids and numbers match the golden story (APR-0007, WO-2026-0153 ...)."""
    inc = run_story(client, users)
    appr = approve(client, users, inc).json()
    refs = {a["kind"]: a["ref_id"] for a in appr["executed_actions"]}
    assert refs == {"speed_change": "L3", "stock_transfer": "STO-4500012", "work_order": "WO-2026-0153"}
    assert appr["id"] == "APR-0007"
    assert client.get(f"/incidents/{inc}", headers=users["op"]).json()["status"] == "executed"


def test_cannot_decide_before_analysis_finished(client, users):
    """Approving while the agents are still working gives 409."""
    client.post("/simulator/scenario/reset", headers=users["admin"])
    client.post("/simulator/scenario/warning", headers=users["admin"])
    r = approve(client, users, "INC-0042")
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"
    assert client.get("/incidents/INC-0042/options", headers=users["op"]).status_code == 404


def test_reject_does_not_execute_and_gives_no_token(client, users):
    """Rejecting runs nothing and issues no approval token."""
    inc = run_story(client, users)
    r = client.post(f"/incidents/{inc}/approve", json={"option_id": "OPT-A", "decision": "reject", "reason": "no"},
                    headers=users["lead"]).json()
    assert r["approval_token"] is None and r["executed_actions"] == []
    assert client.get(f"/incidents/{inc}", headers=users["op"]).json()["status"] == "rejected"
    assert client.app.state.container.maintenance.created == []


def test_unknown_option_is_404(client, users):
    """Approving an option id that does not exist gives 404."""
    inc = run_story(client, users)
    assert approve(client, users, inc, option="OPT-Z").status_code == 404


def test_feedback_only_after_decision_and_updates_confidence(client, users):
    """Feedback is refused before a decision, then updates confidence 0.60 -> 0.82."""
    inc = run_story(client, users)
    body = {"technician_id": "TECH-104", "confirmed": True, "actual_root_cause": "outer race worn"}
    assert client.post(f"/incidents/{inc}/feedback", json=body, headers=users["op"]).status_code == 409
    approve(client, users, inc)
    fb = client.post(f"/incidents/{inc}/feedback", json=body, headers=users["op"]).json()
    assert fb["new_similar_case_confidence"] == 0.82 and fb["id"] == "FB-0031"
    assert client.post(f"/incidents/{inc}/feedback", json=body, headers=users["op"]).status_code == 409


def test_open_incident_is_idempotent_per_asset(client, users):
    """Opening twice for the same asset returns the same incident."""
    client.post("/simulator/scenario/reset", headers=users["admin"])
    a = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "user_question"}, headers=users["op"]).json()
    b = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=users["op"]).json()
    assert a["id"] == b["id"] == "INC-0042"
    assert client.post("/incidents", json={"asset_id": "EQ-9999", "trigger": "ml_alert"},
                       headers=users["op"]).status_code == 404


def test_reconnect_catches_up_without_rerunning(client, users):
    """A reconnecting viewer gets the stored events and the agents do not run again."""
    inc = run_story(client, users)
    all_events = client.get(f"/incidents/{inc}/events", headers=users["op"]).json()
    seqs = [e["seq"] for e in all_events]
    assert seqs == list(range(1, len(seqs) + 1))
    tail = client.get(f"/incidents/{inc}/events", params={"after_seq": seqs[-3]}, headers=users["op"]).json()
    assert [e["seq"] for e in tail] == seqs[-2:]
    token = users["op"]["Authorization"][7:]
    with client.websocket_connect(f"/incidents/{inc}/stream?token={token}&speed=300&after_seq={seqs[-2]}") as ws:
        got = []
        try:
            while True:
                got.append(ws.receive_json()["seq"])
        except Exception:
            pass
    assert got == [seqs[-1]]


def test_drafts_are_rekeyed_to_the_real_incident_id(client, users):
    """Replayed option drafts carry the real incident id, not INC-0042."""
    first = run_story(client, users)
    approve(client, users, first)  # executed -> no longer active, so a new incident can open
    second = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=users["op"]).json()
    assert second["id"] == "INC-0043"
    token = users["op"]["Authorization"][7:]
    with client.websocket_connect(f"/incidents/INC-0043/stream?token={token}&speed=300") as ws:
        try:
            while True:
                ws.receive_text()
        except Exception:
            pass
    opts = client.get("/incidents/INC-0043/options", headers=users["op"]).json()
    assert opts["incident_id"] == "INC-0043"
    drafts = [a["draft"] for o in opts["options"] for a in o["actions"] if "draft" in a]
    assert drafts and all(d["incident_id"] == "INC-0043" for d in drafts)
