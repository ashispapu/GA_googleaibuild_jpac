"""PostgreSQL, Redis and Qdrant behaviour that several processes rely on. Runs against real servers when these are
set (skipped otherwise):
  PLANTGUARD_TEST_PG            postgresql://user@host:port/postgres   (the test creates and drops its own databases)
  PLANTGUARD_TEST_REDIS         redis://host:port/15                    (the test uses its own key prefix)
  PLANTGUARD_TEST_QDRANT_URL    http://host:6333 + PLANTGUARD_TEST_QDRANT_ADMIN_KEY (server with jwt_rbac: true)
"""
import asyncio
import os
import threading
import time
import uuid

import pytest

PG = os.getenv("PLANTGUARD_TEST_PG")
REDIS = os.getenv("PLANTGUARD_TEST_REDIS")
QDRANT = os.getenv("PLANTGUARD_TEST_QDRANT_URL")
QKEY = os.getenv("PLANTGUARD_TEST_QDRANT_ADMIN_KEY")


# ---------------------------------------------------------------- PostgreSQL
@pytest.fixture(scope="module")
def pg_dsn():
    if not PG:
        pytest.skip("PLANTGUARD_TEST_PG not set")
    import psycopg
    name = f"pgtest_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    yield PG.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture(scope="module")
def it_db(pg_dsn):
    from app.sim.db import IT_SCHEMA
    from app.sim.pgdb import PgDatabase
    db = PgDatabase(pg_dsn, IT_SCHEMA, "it-test", max_size=12)
    yield db
    db.close()


def test_pg_id_sequence_is_unique_across_concurrent_callers(it_db):
    got, lock = [], threading.Lock()

    def worker():
        for _ in range(40):
            n = it_db.next_id("TEST", 100)
            with lock:
                got.append(n)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(got) == 320 and len(set(got)) == 320 and min(got) == 100


def test_pg_transaction_rolls_back_together(it_db):
    it_db.set_meta("tx_probe", "before")
    with pytest.raises(RuntimeError):
        with it_db.tx() as c:
            c.execute("UPDATE meta SET value=? WHERE key=?", ("inside", "tx_probe"))
            raise RuntimeError("boom")
    assert it_db.meta("tx_probe") == "before"


def test_pg_session_lock_is_exclusive(it_db):
    order = []

    def hold(tag, wait):
        with it_db.session_lock("plantguard-test-lock"):
            order.append(f"{tag}+")
            time.sleep(wait)
            order.append(f"{tag}-")
    a = threading.Thread(target=hold, args=("a", 0.6))
    a.start()
    time.sleep(0.1)
    hold("b", 0)
    a.join()
    assert order == ["a+", "a-", "b+", "b-"]


def test_incident_save_detects_a_concurrent_change(it_db):
    from datetime import datetime, timezone
    from plantguard_contracts.schemas.incidents import Incident, IncidentStatus, IncidentTrigger, Severity
    from app.repositories.incident_repository import ConcurrentUpdate, IncidentRecord, SharedIncidentRepository
    now = datetime.now(timezone.utc)
    it_db.x("INSERT INTO plants(id, name, location) VALUES('PLANT-A','A','x') ON CONFLICT DO NOTHING")
    it_db.x("INSERT INTO lines(plant_id, id, name, product, rate_units_per_h) VALUES('PLANT-A','L1','Line 1','p',10) ON CONFLICT DO NOTHING")
    it_db.x("INSERT INTO equipment(asset_id, plant_id, line_id, station_id, station_name, station_seq, name, type, model, "
            "manufacturer, pos_x, pos_y) VALUES('EQ-0001','PLANT-A','L1','S1','S1',1,'M','cnc','m','x',0,0) "
            "ON CONFLICT DO NOTHING")
    repo = SharedIncidentRepository(it_db)
    inc = Incident(id="INC-9001", title="t", plant_id="PLANT-A", line_id="L1", asset_id="EQ-0001",
                   trigger=IncidentTrigger.ML_ALERT, severity=Severity.HIGH, status=IncidentStatus.ANALYSING,
                   created_at=now, updated_at=now)
    repo.add(IncidentRecord(incident=inc))
    one, two = repo.get("INC-9001"), repo.get("INC-9001")
    one.incident.title = "first"
    repo.save(one)
    two.incident.title = "second"
    with pytest.raises(ConcurrentUpdate):
        repo.save(two)
    assert repo.get("INC-9001").incident.title == "first"


# ---------------------------------------------------------------- Redis
@pytest.fixture()
def redis_env(monkeypatch):
    if not REDIS:
        pytest.skip("PLANTGUARD_TEST_REDIS not set")
    import app.infrastructure.redis_infra as ri
    monkeypatch.setenv("REDIS_URL", REDIS)
    monkeypatch.setattr(ri, "PREFIX", f"pgtest-{uuid.uuid4().hex[:6]}")
    yield ri
    r = ri.sync_client()
    for k in r.scan_iter(f"{ri.PREFIX}:*"):
        r.delete(k)


def test_redis_bus_reaches_another_process(redis_env):
    from pydantic import BaseModel

    class Msg(BaseModel):
        n: int

    async def run():
        a, b = redis_env.RedisEventBus(), redis_env.RedisEventBus()  # two "processes"
        sub = a.subscribe("incident.X.events")
        await sub.ready()
        await b.publish("incident.X.events", Msg(n=7))
        got = await asyncio.wait_for(sub.__anext__(), 5)
        sub.close()
        return got
    assert asyncio.run(run()) == {"n": 7}


def test_run_queue_gives_each_run_to_one_worker_and_reclaims_a_dead_workers_run(redis_env):
    async def run():
        w1, w2 = redis_env.RunQueue(claim_idle_s=0.5), redis_env.RunQueue(claim_idle_s=0.5)
        await w1.enqueue("INC-1", 1)
        first = await w1.take(block_ms=500)
        assert [f["incident_id"] for _, f, _ in first] == ["INC-1"]
        assert await w2.take(block_ms=200) == []          # not handed out twice
        await asyncio.sleep(0.3)
        await w1.touch(first[0][0])                       # heartbeat keeps it
        await asyncio.sleep(0.3)
        assert await w2.take(block_ms=100) == []
        await asyncio.sleep(0.7)                          # w1 "died": no heartbeat, no ack
        again = await w2.take(block_ms=100)
        assert again and again[0][2] is True and again[0][1]["incident_id"] == "INC-1"
        await w2.ack(again[0][0])
        assert await w2.depth() == 0
    asyncio.run(run())


def test_single_use_claims_and_rate_counters(redis_env):
    kv = redis_env.SharedKV()
    assert kv.claim_once("approval:jti-1:execute", 10) is True
    assert kv.claim_once("approval:jti-1:execute", 10) is False
    assert [kv.hit("client:m", 60) for _ in range(3)] == [1, 2, 3]
    from app.middleware.rate_limit import SharedRateLimiter
    lim = SharedRateLimiter(kv, per_minute=1000, burst=3, burst_window_s=30)

    async def four():
        return [await lim.acquire("Bearer abc") == 0 for _ in range(4)]
    assert asyncio.run(four()) == [True, True, True, False]


def test_only_one_engine_holds_the_lease(redis_env):
    async def run():
        ran = []

        async def work(tag):
            ran.append(tag)
            await asyncio.sleep(10)
        a, b = redis_env.LeaderLease("t", ttl_s=1), redis_env.LeaderLease("t", ttl_s=1)
        ta = asyncio.create_task(a.hold(lambda: work("a")))
        await asyncio.sleep(0.2)
        tb = asyncio.create_task(b.hold(lambda: work("b")))
        await asyncio.sleep(0.8)
        assert ran == ["a"]
        ta.cancel()                                       # engine A stops: B takes over
        await asyncio.sleep(1.2)
        assert ran == ["a", "b"]
        tb.cancel()
    asyncio.run(run())


# ---------------------------------------------------------------- Qdrant server with JWT RBAC
def test_qdrant_tokens_isolate_plants_on_a_real_server():
    if not (QDRANT and QKEY):
        pytest.skip("PLANTGUARD_TEST_QDRANT_URL / _ADMIN_KEY not set")
    from qdrant_client import QdrantClient
    from app.rag.qdrant_store import QdrantStore
    from app.rag.qdrant_tokens import mint
    from app.rag.vector_store import HashingEmbedder
    prefix = f"pgtest_{uuid.uuid4().hex[:6]}"
    emb = HashingEmbedder(dim=64).fit(["a b c"])
    admin = QdrantStore(QdrantClient(url=QDRANT, api_key=QKEY, https=False), emb, prefix)
    admin.ensure(["PLANT-A", "PLANT-B"])
    try:
        admin.add(admin.workorders("PLANT-B"), [(1, "b repair", {"kind": "work_order"})])
        plant_a = QdrantClient(url=QDRANT, api_key=mint(QKEY, "plant", prefix, ["PLANT-A"], 1), https=False)
        assert plant_a.count(admin.workorders("PLANT-A")).count == 0
        for fn in (lambda: plant_a.count(admin.workorders("PLANT-B")),
                   lambda: plant_a.delete(admin.manuals(), points_selector=[1]),
                   lambda: plant_a.create_collection(f"{prefix}_x", vectors_config={"size": 4, "distance": "Cosine"})):
            with pytest.raises(Exception, match="403"):
                fn()
    finally:
        for name in (admin.manuals(), admin.workorders("PLANT-A"), admin.workorders("PLANT-B")):
            admin.client.delete_collection(name)
