"""SSO on the real backend: Keycloak-shaped RS256 tokens, no Keycloak needed (mirrors tests/test_auth_keycloak.py)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.container import build_container
from app.main import create_app
from plantguard_contracts.auth import TokenVerifier
from plantguard_contracts.schemas.security import AuthMode

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_auth_keycloak import ISS, KEY, OTHER, h, kc_token  # noqa: E402


@pytest.fixture()
def kc():
    """A client in AUTH_MODE=keycloak with a local RSA key standing in for Keycloak's signing key."""
    verifier = TokenVerifier(AuthMode.KEYCLOAK, secret="approval-secret-0123456789abcdef", issuer=ISS,
                             audience="plantguard-api", client_id="plantguard-web", public_key=KEY.public_key())
    return TestClient(create_app(build_container(verifier=verifier)))


def test_config_and_local_login_off(kc):
    """/auth/config advertises keycloak and POST /auth/token answers 501."""
    assert kc.get("/auth/config").json()["mode"] == "keycloak"
    r = kc.post("/auth/token", json={"username": "lead", "password": "demo"})
    assert r.status_code == 501 and r.json()["code"] == "NOT_IMPLEMENTED"


def test_token_mapping_and_strongest_role(kc):
    """Keycloak roles map to backend roles; with several roles the strongest wins."""
    me = kc.get("/auth/me", headers=h(kc_token())).json()
    assert me["id"] == "USR-lead" and me["role"] == "maintenance_lead"
    assert kc.get("/auth/me", headers=h(kc_token(roles=["operator", "plant_manager"]))).json()["role"] == "plant_manager"


@pytest.mark.parametrize("bad", [dict(key=OTHER), dict(aud=("account",)), dict(iss="http://evil/realms/plantguard"),
                                 dict(exp_in=-120), dict(roles=["offline_access"])])
def test_bad_tokens_rejected(kc, bad):
    """Wrong issuer, audience, signature or an expired token all give 401."""
    r = kc.get("/auth/me", headers=h(kc_token(**bad)))
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


def test_rbac_scope_and_websocket(kc):
    """Backend RBAC and plant scope apply to Keycloak users, REST and WebSocket."""
    op = kc_token(user="op", role="operator")
    r = kc.post("/incidents/INC-0042/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "x"},
                headers=h(op))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    assert kc.get("/workforce/shifts", params={"plant_id": "PLANT-B"}, headers=h(kc_token())).json()["code"] == "PLANT_SCOPE"
    with kc.websocket_connect(f"/ot/stream?token={kc_token()}&line_id=L3") as ws:
        assert json.loads(ws.receive_text())["line_id"] == "L3"


def test_websocket_rejects_bad_token(kc):
    """A WebSocket with a bad token is refused."""
    with pytest.raises(Exception):
        with kc.websocket_connect(f"/ot/stream?token={kc_token(key=OTHER)}&line_id=L3") as ws:
            ws.receive_text()
