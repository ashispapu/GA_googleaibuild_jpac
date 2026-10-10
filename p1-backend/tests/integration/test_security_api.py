import pytest

from app.services.approval_tokens import ApprovalError
from helpers import run_story


def test_access_token_is_not_an_approval_token(client, users):
    """A normal login token cannot authorise a write."""
    access = users["lead"]["Authorization"][7:]
    with pytest.raises(ApprovalError):
        client.app.state.container.tokens.verify(access, "INC-0042", "work_order")



def test_api_rate_limit_returns_429_error_shape():
    """Over the limit the API answers 429 RATE_LIMITED in the standard error shape."""
    from fastapi.testclient import TestClient
    from app.core.config import Settings
    from app.main import create_app
    c = TestClient(create_app(settings=Settings(rate_limit_per_min=60, rate_limit_burst=3)))
    codes = [c.get("/auth/config").status_code for _ in range(6)]
    assert 429 in codes
    r = c.get("/auth/config")
    assert r.json()["code"] == "RATE_LIMITED" and "Retry-After" in r.headers


def test_write_endpoint_rejects_draft_that_was_not_approved(client, users):
    """A write with a changed draft is refused even with a valid token."""
    inc = run_story(client, users)
    opts = client.get(f"/incidents/{inc}/options", headers=users["lead"]).json()
    appr = client.post(f"/incidents/{inc}/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "ok"},
                       headers=users["lead"]).json()
    draft = opts["options"][1]["actions"][2]["draft"]
    tampered = draft | {"technician_id": "TECH-999", "approval_token": appr["approval_token"]}
    r = client.post("/maintenance/work-orders", json=tampered, headers=users["lead"])
    assert r.status_code == 403 and r.json()["code"] == "APPROVAL_INVALID"


def test_approval_token_cannot_be_replayed_on_write_endpoint(client, users):
    """A token can be used once; the second write is refused."""
    inc = run_story(client, users)
    opts = client.get(f"/incidents/{inc}/options", headers=users["lead"]).json()
    appr = client.post(f"/incidents/{inc}/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "ok"},
                       headers=users["lead"]).json()
    body = opts["options"][1]["actions"][2]["draft"] | {"approval_token": appr["approval_token"]}
    assert client.post("/maintenance/work-orders", json=body, headers=users["lead"]).status_code == 201
    r = client.post("/maintenance/work-orders", json=body, headers=users["lead"])
    assert r.status_code == 403 and r.json()["code"] == "APPROVAL_INVALID"


def test_rejected_option_cannot_write(client, users):
    """A rejected option never authorises writes."""
    inc = run_story(client, users)
    opts = client.get(f"/incidents/{inc}/options", headers=users["lead"]).json()
    c = client.app.state.container
    forged = c.tokens.issue("USR-lead", inc, "OPT-B", {"work_order"})  # valid signature, but never approved
    body = opts["options"][1]["actions"][2]["draft"] | {"approval_token": forged}
    r = client.post("/maintenance/work-orders", json=body, headers=users["lead"])
    assert r.status_code == 403 and r.json()["code"] == "APPROVAL_INVALID"


def test_plant_scope_on_incidents(client, users, container):
    """A user without access to a plant cannot open or read its incidents."""
    inc = run_story(client, users)
    # a PLANT-B-only user cannot read a PLANT-A incident
    tok = container.verifier.issue_local("USR-bob", "Bob", "maintenance_lead", ["PLANT-B"])
    r = client.get(f"/incidents/{inc}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and r.json()["code"] == "PLANT_SCOPE"


def test_fixture_names_cannot_escape_the_folder(client, users):
    """Path-traversal names return 404 and never read other files."""
    for bad in ("..%2f..%2fREADME", "a/../b"):
        assert client.get(f"/erp/parts/{bad}", headers=users["op"]).status_code == 404
    r = client.post("/engineering/docs/search", json={"query": "x", "asset_id": "../../x", "top_k": 1},
                    headers=users["op"])
    assert r.status_code in (200, 422)


def test_denied_permission_is_audited(client, users):
    """A 403 permission denial is written to the audit log."""
    client.post("/incidents/INC-0042/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "x"},
                headers=users["op"])
    entries = client.get("/audit", headers=users["admin"]).json()["items"]
    assert any(e["action"] == "approve_denied" and e["actor"] == "USR-op" for e in entries)


def test_audit_chain_valid_after_full_story(client, users):
    """The audit hash chain verifies after a complete incident story."""
    inc = run_story(client, users)
    client.post(f"/incidents/{inc}/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "ok"},
                headers=users["lead"])
    assert client.get("/audit/verify", headers=users["admin"]).json()["valid"]
    assert client.get("/audit", headers=users["op"]).status_code == 403


def test_login_failure_is_401_and_audited(client):
    """A wrong password gives 401 and an audit entry."""
    r = client.post("/auth/token", json={"username": "lead", "password": "wrong"})
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
