"""Mint Qdrant JWT access tokens (Qdrant `jwt_rbac`): each PlantGuard service gets a token that names exactly the
collections it may use, signed with the Qdrant API key (only the migrate job and operators hold that key).

  python -m app.rag.qdrant_tokens --role app --plants PLANT-A,PLANT-B [--prefix plantguard_prod] [--days 90]
  python -m app.rag.qdrant_tokens --role plant --plants PLANT-A          (a plant-local integration: its own repairs only)
  python -m app.rag.qdrant_tokens --role readonly --plants PLANT-A,PLANT-B  (analysts / dashboards)

Roles
  app       api / engine / worker: read manuals, read + write every plant's repair collection
  plant     read manuals, read + write ONE plant's repair collection, nothing else
  readonly  read manuals and the listed plants' repair collections
Collections that are not named in the token cannot be read, written, listed or created with it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import jwt

from app.rag.qdrant_store import vector_prefix


def collection_names(prefix: str, plants: list[str]) -> tuple[str, list[str]]:
    manuals = f"{prefix}_manuals"
    return manuals, [f"{prefix}_workorders_{p.lower().replace('-', '_')}" for p in plants]


def claims_for(role: str, prefix: str, plants: list[str], days: float) -> dict:
    manuals, workorders = collection_names(prefix, plants)
    if role == "plant" and len(plants) != 1:
        raise ValueError("role plant takes exactly one plant")
    write = "rw" if role in ("app", "plant") else "r"
    access = [{"collection": manuals, "access": "r"}] + [{"collection": c, "access": write} for c in workorders]
    out: dict = {"access": access, "sub": f"plantguard-{role}"}
    if days > 0:
        out["exp"] = int(time.time() + days * 86400)
    return out


def mint(secret: str, role: str, prefix: str, plants: list[str], days: float = 90) -> str:
    return jwt.encode(claims_for(role, prefix, plants, days), secret, algorithm="HS256")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", choices=("app", "plant", "readonly"), required=True)
    ap.add_argument("--plants", required=True, help="comma-separated plant ids, e.g. PLANT-A,PLANT-B")
    ap.add_argument("--prefix", default=None, help="collection prefix (default PLANTGUARD_VECTOR_PREFIX or plantguard_<env>)")
    ap.add_argument("--days", type=float, default=90, help="lifetime in days (0 = no expiry; rotate it yourself)")
    a = ap.parse_args(argv)
    secret = os.getenv("QDRANT_ADMIN_API_KEY")
    if not secret:
        print("set QDRANT_ADMIN_API_KEY (the Qdrant service.api_key) to sign the token", file=sys.stderr)
        return 2
    print(mint(secret, a.role, a.prefix or vector_prefix(), [p.strip() for p in a.plants.split(",") if p.strip()],
               a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
