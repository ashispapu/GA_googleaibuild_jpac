"""Shared state for running PlantGuard as several processes (Cloud Run: api x N, engine x 1, worker x N).

Production uses Memorystore for Redis (private IP, AUTH, in-transit TLS: `rediss://:<auth>@host:6378/0`).
Everything here has an in-process twin used when REDIS_URL is not set (one process, local development, tests).

  RedisEventBus   pub/sub fan-out of the C8 channels (telemetry, health, incident events/status, garden ...) to every
                  process; same interface as InMemoryEventBus.
  RunQueue        analysis runs as a Redis Stream with a consumer group: each run is taken by exactly one worker;
                  runs of a crashed worker are re-claimed after `claim_idle_s`.
  SharedKV        small helpers on top of one sync Redis client: latest health scores, garden positions, single-use
                  approval tokens, rate-limit counters.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator

from pydantic import BaseModel

log = logging.getLogger("plantguard.redis")
PREFIX = os.getenv("PLANTGUARD_REDIS_PREFIX", "plantguard")
_CLOSED = object()


def redis_url() -> str | None:
    """REDIS_URL, or the parts (Cloud Run: the AUTH string comes from Secret Manager as its own variable):
    REDIS_HOST, REDIS_PORT (6378 with TLS, else 6379), REDIS_AUTH, REDIS_TLS (true), REDIS_DB (0)."""
    url = os.getenv("REDIS_URL")
    if url:
        return url
    host = os.getenv("REDIS_HOST")
    if not host:
        return None
    from urllib.parse import quote
    tls = os.getenv("REDIS_TLS", "true").lower() in ("1", "true", "yes")
    auth = os.getenv("REDIS_AUTH")
    port = os.getenv("REDIS_PORT", "6378" if tls else "6379")
    return f"{'rediss' if tls else 'redis'}://{':' + quote(auth, safe='') + '@' if auth else ''}{host}:{port}/{os.getenv('REDIS_DB', '0')}"


def _ssl_kwargs() -> dict:
    """Memorystore in-transit encryption: rediss:// plus the instance CA (REDIS_CA_CERT = path to server-ca.pem)."""
    ca = os.getenv("REDIS_CA_CERT")
    return {"ssl_ca_certs": ca} if ca else {}


def sync_client():
    import redis
    return redis.Redis.from_url(redis_url(), decode_responses=True, health_check_interval=30, socket_timeout=5,
                                socket_connect_timeout=5, retry_on_timeout=True, **_ssl_kwargs())


def async_client(socket_timeout: float = 10):
    """socket_timeout must stay above any blocking read (XREADGROUP BLOCK) made on this client."""
    import redis.asyncio as aredis
    return aredis.Redis.from_url(redis_url(), decode_responses=True, health_check_interval=30, socket_timeout=socket_timeout,
                                 socket_connect_timeout=5, retry_on_timeout=True, **_ssl_kwargs())


# ---------------------------------------------------------------- event bus
class RedisSubscription:
    def __init__(self, bus: "RedisEventBus", channel: str):
        self._bus, self.channel = bus, channel
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._ready = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item

    async def ready(self) -> None:
        """Wait until Redis confirmed the subscription (so nothing published afterwards can be missed)."""
        await asyncio.wait_for(self._ready.wait(), 10)

    def deliver(self, payload: dict) -> None:
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:  # a stuck viewer must never stall the bus: drop its oldest message
            self._queue.get_nowait()
            self._queue.put_nowait(payload)

    def close(self) -> None:
        self._bus._drop(self)
        self._queue.put_nowait(_CLOSED)


class RedisEventBus:
    """Publish = Redis PUBLISH (JSON). One pub/sub connection per process fans messages out to local subscribers."""

    def __init__(self):
        self._pub = async_client()
        self._sub_client = async_client()
        self._pubsub = None
        self._subs: dict[str, set[RedisSubscription]] = defaultdict(set)
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _key(self, channel: str) -> str:
        return f"{PREFIX}:bus:{channel}"

    async def publish(self, channel: str, message: BaseModel) -> None:
        await self._pub.publish(self._key(channel), json.dumps(message.model_dump(mode="json")))

    def subscribe(self, channel: str) -> RedisSubscription:
        sub = RedisSubscription(self, channel)
        first = not self._subs[channel]
        self._subs[channel].add(sub)
        asyncio.get_running_loop().create_task(self._ensure(channel, sub, first))
        return sub

    async def _ensure(self, channel: str, sub: RedisSubscription, first: bool) -> None:
        async with self._lock:
            if self._pubsub is None:
                self._pubsub = self._sub_client.pubsub(ignore_subscribe_messages=True)
            if first or self._key(channel) not in self._pubsub.channels:
                await self._pubsub.subscribe(self._key(channel))
            if self._reader is None or self._reader.done():
                self._reader = asyncio.get_running_loop().create_task(self._read())
        sub._ready.set()

    async def _read(self) -> None:
        cut = len(PREFIX) + 5
        while True:
            try:
                msg = await self._pubsub.get_message(timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("redis bus read failed; reconnecting")
                await asyncio.sleep(1)
                continue
            if msg is None or msg.get("type") != "message":
                continue
            channel = msg["channel"][cut:]
            try:
                payload = json.loads(msg["data"])
            except ValueError:
                continue
            for sub in list(self._subs.get(channel, ())):
                sub.deliver(payload)

    def _drop(self, sub: RedisSubscription) -> None:
        subs = self._subs.get(sub.channel)
        if subs is None:
            return
        subs.discard(sub)
        if not subs and self._pubsub is not None:
            try:
                asyncio.get_running_loop().create_task(self._pubsub.unsubscribe(self._key(sub.channel)))
            except RuntimeError:
                pass


# ---------------------------------------------------------------- run queue
class RunQueue:
    """Redis Stream `<prefix>:runs`, consumer group `workers`. Entry = {incident_id, round}."""

    GROUP = "workers"

    def __init__(self, claim_idle_s: float = 300.0):
        self.r = async_client(socket_timeout=30)  # take() blocks up to 5 s
        self.stream = f"{PREFIX}:runs"
        self.consumer = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.claim_idle_ms = int(claim_idle_s * 1000)
        self._group_ready = False

    async def _group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.r.xgroup_create(self.stream, self.GROUP, id="0", mkstream=True)
        except Exception as e:  # BUSYGROUP: exists already
            if "BUSYGROUP" not in str(e):
                raise
        self._group_ready = True

    async def enqueue(self, incident_id: str, rnd: int) -> None:
        await self._group()
        await self.r.xadd(self.stream, {"incident_id": incident_id, "round": str(rnd)}, maxlen=10000, approximate=True)

    async def take(self, block_ms: int = 5000) -> list[tuple[str, dict, bool]]:
        """[(entry id, fields, reclaimed?)]. Re-claims runs a crashed worker left unacknowledged first."""
        await self._group()
        out = []
        try:
            res = await self.r.xautoclaim(self.stream, self.GROUP, self.consumer, self.claim_idle_ms, "0-0", count=5)
            for eid, fields in (res[1] if res else []):
                if fields:
                    out.append((eid, fields, True))
        except Exception:
            log.debug("xautoclaim not available", exc_info=True)
        if out:
            return out
        res = await self.r.xreadgroup(self.GROUP, self.consumer, {self.stream: ">"}, count=1, block=block_ms)
        for _, entries in res or []:
            for eid, fields in entries:
                out.append((eid, fields, False))
        return out

    async def touch(self, entry_id: str) -> None:
        """Heartbeat for a long run: reset the entry's idle time so no other worker re-claims it."""
        await self.r.xclaim(self.stream, self.GROUP, self.consumer, 0, [entry_id], justid=True)

    async def ack(self, entry_id: str) -> None:
        await self.r.xack(self.stream, self.GROUP, entry_id)
        await self.r.xdel(self.stream, entry_id)

    async def depth(self) -> int:
        try:
            return int(await self.r.xlen(self.stream))
        except Exception:
            return -1


# ---------------------------------------------------------------- leader lease
class LeaderLease:
    """Only one process may drive the plant (tick the simulation, open incidents from alerts, react to alarms).
    Cloud Run can briefly run two engine instances during a deploy; the lease makes the second one wait.
    SET NX EX `ttl_s`, renewed every ttl/3; losing it stops the guarded work."""

    def __init__(self, name: str, ttl_s: int = 15):
        self.r = async_client()
        self.key = f"{PREFIX}:lease:{name}"
        self.me = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.ttl = ttl_s

    _RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
    _RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"

    async def hold(self, *work) -> None:
        """Wait for the lease, then run the zero-arg coroutine factories in `work` until the lease is lost; repeat."""
        while True:
            try:
                got = await self.r.set(self.key, self.me, nx=True, ex=self.ttl)
            except Exception:
                log.exception("lease %s: redis unavailable", self.key)
                got = False
            if not got:
                await asyncio.sleep(self.ttl / 3)
                continue
            log.warning("lease %s acquired by %s: this process drives the plant", self.key, self.me)
            tasks = [asyncio.create_task(w()) for w in work]
            try:
                while True:
                    await asyncio.sleep(self.ttl / 3)
                    try:
                        ok = await self.r.eval(self._RENEW, 1, self.key, self.me, self.ttl)
                    except Exception:
                        log.exception("lease %s: renew failed", self.key)
                        ok = 0
                    if not ok:
                        log.error("lease %s lost; stopping the plant loop here", self.key)
                        break
            finally:
                for t in tasks:
                    t.cancel()
                try:
                    await self.r.eval(self._RELEASE, 1, self.key, self.me)
                except Exception:
                    pass

    async def holder(self) -> str | None:
        return await self.r.get(self.key)


# ---------------------------------------------------------------- key/value
class SharedKV:
    def __init__(self):
        self.r = sync_client()

    def key(self, *parts: str) -> str:
        return ":".join((PREFIX, *parts))

    # health scores (engine writes, every API instance reads)
    def put_json(self, hash_name: str, field: str, value: Any) -> None:
        self.r.hset(self.key(hash_name), field, json.dumps(value, default=str))

    def put_many_json(self, hash_name: str, values: dict[str, Any]) -> None:
        if values:
            self.r.hset(self.key(hash_name), mapping={k: json.dumps(v, default=str) for k, v in values.items()})

    def get_json(self, hash_name: str, field: str) -> Any:
        v = self.r.hget(self.key(hash_name), field)
        return json.loads(v) if v else None

    def all_json(self, hash_name: str) -> dict[str, Any]:
        return {k: json.loads(v) for k, v in self.r.hgetall(self.key(hash_name)).items()}

    def delete(self, hash_name: str, field: str) -> None:
        self.r.hdel(self.key(hash_name), field)

    # single-use markers (approval tokens)
    def claim_once(self, name: str, ttl_s: int) -> bool:
        """True the first time `name` is claimed (atomic SET NX), False afterwards."""
        return bool(self.r.set(self.key("once", name), "1", nx=True, ex=max(1, int(ttl_s))))

    # fixed-window counters (rate limiting)
    def hit(self, name: str, window_s: int) -> int:
        k = self.key("rl", name)
        pipe = self.r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window_s, nx=True)
        return int(pipe.execute()[0])

    def ping(self) -> bool:
        return bool(self.r.ping())
