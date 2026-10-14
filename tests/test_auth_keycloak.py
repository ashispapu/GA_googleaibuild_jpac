"""SSO contract: Keycloak-mode tokens, without needing a running Keycloak.
We sign tokens shaped exactly like Keycloak's with a local RSA key and check the backend's behaviour."""
import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import mock_server.app as mock
from plantguard_contracts.auth import TokenVerifier
from plantguard_contracts.schemas.security import AuthMode, KeycloakClaims, ROLE_PERMISSIONS, Role

ISS = "http://localhost:8080/realms/plantguard"
ROOT = Path(__file__).parent.parent
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def kc_token(user="lead", role="maintenance_lead", plants=("PLANT-A",), aud=("plantguard-api", "account"),
             iss=ISS, exp_in=600, key=KEY, roles=None):
    t = int(time.time())
    body = {"iss": iss, "aud": list(aud), "exp": t + exp_in, "iat": t, "sub": "5b0c1e2a-uuid", "azp": "plantguard-web",
            "typ": "Bearer", "preferred_username": user, "name": f"{user} name", "user_id": f"USR-{user}",
            "plant_ids": list(plants), "roles": roles if roles is not None else [role, "offline_access", "default-roles-plantguard"]}
    return jwt.encode(body, key, algorithm="RS256", headers={"kid": "test"})


@pytest.fixture()
def kc():
    old = mock.VERIFIER
    mock.VERIFIER = TokenVerifier(AuthMode.KEYCLOAK, secret="approval-secret", issuer=ISS, audience="plantguard-api",
                                  client_id="plantguard-web", public_key=KEY.public_key())
    yield TestClient(mock.app)
    mock.VERIFIER = old


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_config_tells_ui_to_use_keycloak(kc):
    cfg = kc.get("/auth/config").json()
    assert cfg["mode"] == "keycloak" and cfg["client_id"] == "plantguard-web"
    assert cfg["authorize_url"] == f"{ISS}/protocol/openid-connect/auth"


def test_local_login_is_off(kc):
    r = kc.post("/auth/token", json={"username": "lead", "password": "demo"})
    assert r.status_code == 501 and r.json()["code"] == "NOT_IMPLEMENTED"


def test_keycloak_token_maps_to_our_claims(kc):
    me = kc.get("/auth/me", headers=h(kc_token())).json()
    assert me["id"] == "USR-lead" and me["role"] == "maintenance_lead" and me["plant_ids"] == ["PLANT-A"]
    assert "approve" in me["permissions"]


def test_strongest_role_wins(kc):
    me = kc.get("/auth/me", headers=h(kc_token(roles=["operator", "plant_manager"]))).json()
    assert me["role"] == "plant_manager"


@pytest.mark.parametrize("bad", [
    dict(key=OTHER),                       # not signed by Keycloak
    dict(aud=("account",)),                # token not meant for our API
    dict(iss="http://evil/realms/plantguard"),
    dict(exp_in=-120),                     # expired (beyond 30 s leeway)
    dict(roles=["offline_access"]),        # valid user, but no PlantGuard role
])
def test_bad_tokens_rejected(kc, bad):
    r = kc.get("/auth/me", headers=h(kc_token(**bad)))
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


def test_rbac_and_scope_unchanged_with_keycloak(kc):
    op = kc_token(user="op", role="operator")
    r = kc.post("/incidents/INC-0042/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "x"},
                headers=h(op))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    r = kc.get("/workforce/shifts", params={"plant_id": "PLANT-B"}, headers=h(kc_token()))
    assert r.json()["code"] == "PLANT_SCOPE"


def test_approval_still_backend_signed(kc):
    admin = h(kc_token(user="admin", role="admin", plants=("PLANT-A", "PLANT-B")))
    kc.post("/simulator/scenario/reset", headers=admin)
    kc.post("/simulator/scenario/warning", headers=admin)
    appr = kc.post("/incidents/INC-0042/approve", json={"option_id": "OPT-B", "decision": "approve", "reason": "ok"},
                   headers=h(kc_token())).json()
    claims = jwt.decode(appr["approval_token"], "approval-secret", algorithms=["HS256"])
    assert claims["typ"] == "approval" and claims["sub"] == "USR-lead"


def test_websocket_accepts_keycloak_token(kc):
    with kc.websocket_connect(f"/ot/stream?token={kc_token()}&line_id=L3") as ws:
        assert json.loads(ws.receive_text())["line_id"] == "L3"


def test_realm_file_matches_contract():
    realm = json.loads((ROOT / "config/keycloak/plantguard-realm.json").read_text())
    users = json.loads((ROOT / "fixtures/story/users.json").read_text())
    assert {r.value for r in Role} <= {r["name"] for r in realm["roles"]["realm"]}
    web = next(c for c in realm["clients"] if c["clientId"] == "plantguard-web")
    kinds = {m["name"] for m in web["protocolMappers"]}
    assert {"audience-plantguard-api", "user_id", "plant_ids", "roles"} <= kinds
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    by_name = {u["username"]: u for u in realm["users"]}
    for u in users:  # same people, ids, roles and plants in both modes
        k = by_name[u["username"]]
        assert k["attributes"]["user_id"] == [u["id"]]
        assert k["attributes"]["plant_ids"] == u["plant_ids"]
        assert u["role"] in k["realmRoles"]
    # a token shaped by these mappers parses into the contract model
    KeycloakClaims(iss=ISS, aud=["plantguard-api"], exp=1, iat=1, sub="x", preferred_username="op",
                   user_id="USR-op", plant_ids=["PLANT-A"], roles=["operator"])
