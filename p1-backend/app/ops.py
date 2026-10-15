"""Operations commands (Cloud Run Jobs / one-off maintenance).

  python -m app.ops migrate     create/upgrade both schemas, seed a new plant, build the vector index (idempotent)
  python -m app.ops reindex     rebuild the vector index from the manuals + every closed repair
  python -m app.ops snapshot    Qdrant snapshot of every PlantGuard collection (copied to Cloud Storage by the job)
  python -m app.ops check       print what this process can reach (databases, Redis, Qdrant, LLM settings)

Exit code 0 = done, 1 = failed (the job is marked failed and retried by Cloud Run).
"""
from __future__ import annotations

import json
import logging
import os
import sys

from app.core.observability import setup_logging

log = logging.getLogger("plantguard.ops")


def _settings_and_cfg():
    from app.core.config import Settings
    from app.sim.settings import load_sim_settings
    s = Settings.from_env()
    return s, load_sim_settings(s.fixtures_dir.parents[1], s.sim_ini)


def migrate() -> None:
    os.environ["PLANTGUARD_ROLE"] = "migrate"
    from app.container import build_container
    c = build_container()
    print(json.dumps({"migrated": True, "plant_time": c.clock().isoformat(),
                      "vector_store": c.retriever.backend.name, "chunks": c.retriever.backend.size()}))


def reindex() -> None:
    from app.rag.corpus import load_manuals
    from app.rag.retriever import Retriever, backend_factory
    from app.sim.clock import iso
    from app.sim.db import open_databases
    from datetime import datetime, timezone
    s, cfg = _settings_and_cfg()
    _ot, it = open_databases(cfg.data_dir)
    root = s.fixtures_dir.parents[1]
    with it.session_lock("plantguard-vector-index"):
        r = Retriever.build(it, load_manuals(root / "p4-ml-data" / "corpus" / "manuals"),
                            iso(datetime.now(timezone.utc)), backend_factory(it, cfg.data_dir, recreate=True))
    print(json.dumps({"reindexed": True, "vector_store": r.backend.name, "chunks": r.backend.size()}))


def snapshot() -> None:
    from app.rag.retriever import open_backend
    from app.sim.db import open_databases
    _s, cfg = _settings_and_cfg()
    _ot, it = open_databases(cfg.data_dir)
    backend = open_backend(it, cfg.data_dir)
    if backend is None or backend.name != "qdrant":
        raise SystemExit("snapshot needs the Qdrant vector store (QDRANT_URL) with an index")
    print(json.dumps({"snapshots": backend.store.snapshot(backend.collections())}))


def check() -> None:
    from app.infrastructure.redis_infra import SharedKV, redis_url
    from app.rag.retriever import open_backend, vector_mode
    from app.sim.db import database_urls, open_databases
    _s, cfg = _settings_and_cfg()
    out: dict = {"role": os.getenv("PLANTGUARD_ROLE", "all"), "env": os.getenv("PLANTGUARD_ENV", "dev")}
    try:
        ot, it = open_databases(cfg.data_dir)
        out["databases"] = {"kind": "postgres" if all(database_urls()) else "sqlite", "ot": ot.ping(), "it": it.ping(),
                            "seeded_at": it.meta("seeded_at")}
    except Exception as e:  # noqa: BLE001
        out["databases"] = f"failing: {e}"
        it = None
    try:
        if redis_url():
            import asyncio
            from app.infrastructure.redis_infra import LeaderLease, RunQueue
            out["redis"] = SharedKV().ping()
            out["run_queue_depth"] = asyncio.run(RunQueue().depth())
            out["engine_lease_holder"] = asyncio.run(LeaderLease("plant-engine").holder())
        else:
            out["redis"] = "not configured (single process)"
    except Exception as e:  # noqa: BLE001
        out["redis"] = f"failing: {e}"
    try:
        b = open_backend(it, cfg.data_dir) if it is not None else None
        out["vector_store"] = {"mode": vector_mode(), "chunks": b.size() if b else 0}
    except Exception as e:  # noqa: BLE001
        out["vector_store"] = f"failing: {e}"
    print(json.dumps(out, indent=2, default=str))


def main(argv: list[str]) -> int:
    setup_logging()
    cmds = {"migrate": migrate, "reindex": reindex, "snapshot": snapshot, "check": check}
    if len(argv) != 1 or argv[0] not in cmds:
        print(__doc__)
        return 2
    try:
        cmds[argv[0]]()
        return 0
    except SystemExit:
        raise
    except Exception:
        log.exception("%s failed", argv[0])
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
