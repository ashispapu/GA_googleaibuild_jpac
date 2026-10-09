"""After `docker compose up`, prove SSO works end to end (1 command, P1 runs this once):
    PYTHONPATH=. AUTH_MODE=keycloak python config/keycloak/check_keycloak.py
Logs in each demo user via the dev client, verifies the token with the SAME TokenVerifier the backend uses."""
import json, os, sys

import httpx

from plantguard_contracts.auth import TokenVerifier

base = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
realm = os.getenv("KEYCLOAK_REALM", "plantguard")
users = json.load(open("fixtures/story/users.json"))
v = TokenVerifier.from_env()
ok = True
for u in users:
    r = httpx.post(f"{base}/realms/{realm}/protocol/openid-connect/token",
                   data={"grant_type": "password", "client_id": "plantguard-dev",
                         "username": u["username"], "password": u["password"]})
    try:
        r.raise_for_status()
        c = v.verify(r.json()["access_token"])
        good = c.sub == u["id"] and c.role.value == u["role"] and c.plant_ids == u["plant_ids"]
        print(("OK  " if good else "BAD ") + f"{u['username']}: {c.sub} {c.role.value} {c.plant_ids}")
        ok &= good
    except Exception as e:
        print(f"FAIL {u['username']}: {e}")
        ok = False
sys.exit(0 if ok else 1)
