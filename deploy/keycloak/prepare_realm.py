"""Turn the development realm (config/keycloak/plantguard-realm.json) into a production realm file.

  python deploy/keycloak/prepare_realm.py --web-url https://plantguard-web-xxxx.a.run.app \
      --client-secret-file secret.txt --out realm.json [--drop-users | --temp-passwords users.csv]

Changes made:
  - plantguard-web: redirect / logout / web-origin URLs = the production web app; new client secret
  - plantguard-dev (password grant, for local tests) is removed
  - realm: SSL required for every connection, brute-force protection, password policy, shorter sessions
  - users: dropped (--drop-users; create real ones in the admin console) or kept with a random temporary password
    each (written to --temp-passwords as CSV; Keycloak forces a new password at first sign-in). Real users need the
    attributes `user_id` (the PlantGuard user id, e.g. USR-lead) and `plant_ids`, and one realm role.
"""
from __future__ import annotations

import argparse
import csv
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare(realm: dict, web_url: str, client_secret: str, drop_users: bool) -> tuple[dict, list[tuple[str, str]]]:
    web = web_url.rstrip("/")
    realm["sslRequired"] = "all"
    realm["bruteForceProtected"] = True
    realm["permanentLockout"] = False
    realm["failureFactor"] = 5
    realm["passwordPolicy"] = "length(12) and notUsername and notEmail and passwordHistory(3)"
    realm["ssoSessionIdleTimeout"] = 1800
    realm["ssoSessionMaxLifespan"] = 36000
    realm["accessTokenLifespan"] = 900
    realm["revokeRefreshToken"] = True
    realm["refreshTokenMaxReuse"] = 0
    realm["clients"] = [c for c in realm["clients"] if c["clientId"] != "plantguard-dev"]
    for c in realm["clients"]:
        if c["clientId"] == "plantguard-web":
            c["secret"] = client_secret
            c["redirectUris"] = [f"{web}/*"]
            c["webOrigins"] = [web]
            c.setdefault("attributes", {})["post.logout.redirect.uris"] = f"{web}/*"
            c["directAccessGrantsEnabled"] = False
    issued: list[tuple[str, str]] = []
    if drop_users:
        realm["users"] = []
    else:
        for u in realm.get("users", []):
            pw = secrets.token_urlsafe(12) + "!9a"
            u["credentials"] = [{"type": "password", "value": pw, "temporary": True}]
            u["requiredActions"] = ["UPDATE_PASSWORD"]
            issued.append((u["username"], pw))
    return realm, issued


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web-url", required=True)
    sec = ap.add_mutually_exclusive_group(required=True)
    sec.add_argument("--client-secret")
    sec.add_argument("--client-secret-file", help="file holding the secret (keeps it off the command line)")
    ap.add_argument("--src", default=str(ROOT / "config" / "keycloak" / "plantguard-realm.json"))
    ap.add_argument("--out", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--drop-users", action="store_true")
    g.add_argument("--temp-passwords", help="CSV file for the one-time passwords of the kept users")
    a = ap.parse_args(argv)
    if a.client_secret_file:
        a.client_secret = Path(a.client_secret_file).read_text(encoding="utf-8-sig").strip()
    if len(a.client_secret) < 32:
        print("--client-secret must be at least 32 characters", file=sys.stderr)
        return 2
    realm, issued = prepare(json.loads(Path(a.src).read_text()), a.web_url, a.client_secret, a.drop_users)
    Path(a.out).write_text(json.dumps(realm, indent=2))
    if a.temp_passwords:
        with open(a.temp_passwords, "w", newline="") as f:
            csv.writer(f).writerows([("username", "temporary_password"), *issued])
        print(f"{len(issued)} users kept; one-time passwords in {a.temp_passwords} (hand out, then delete the file)")
    print(f"realm written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
