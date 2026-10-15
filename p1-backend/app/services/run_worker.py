"""Analysis worker (PLANTGUARD_ROLE=worker, or `all` with a shared queue): takes runs from the Redis run queue and
runs the agents for them. Each entry is acknowledged only after its run finished, so a run whose worker died is
re-claimed by another worker (RunQueue.claim_idle_s); a heartbeat keeps long runs from being re-claimed."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("plantguard.worker")


async def run_worker(runs, queue, concurrency: int = 4, heartbeat_s: float = 30.0) -> None:
    slots = asyncio.Semaphore(max(1, concurrency))
    active: set[asyncio.Task] = set()

    async def one(entry_id: str, fields: dict, reclaimed: bool) -> None:
        async def beat():
            while True:
                await asyncio.sleep(heartbeat_s)
                try:
                    await queue.touch(entry_id)
                except Exception:
                    log.warning("heartbeat for %s failed", entry_id, exc_info=True)
        hb = asyncio.create_task(beat())
        try:
            incident_id, rnd = fields["incident_id"], int(fields["round"])
            log.info("worker: analysing %s round %d%s", incident_id, rnd, " (re-claimed)" if reclaimed else "")
            await runs.run_now(incident_id, rnd, reclaimed)
        except asyncio.CancelledError:
            raise  # shutting down: leave it un-acked, another worker re-claims it
        except Exception:
            log.exception("worker: run %s failed", fields)
        else:
            await queue.ack(entry_id)
            return
        finally:
            hb.cancel()
            slots.release()
        try:
            await queue.ack(entry_id)  # a run that crashed has already recorded its error + `finished`
        except Exception:
            log.exception("worker: ack failed")

    log.info("analysis worker started (%d at a time)", concurrency)
    try:
        while True:
            await slots.acquire()
            try:
                entries = await queue.take(block_ms=5000)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker: queue unavailable")
                slots.release()
                await asyncio.sleep(2)
                continue
            if not entries:
                slots.release()
                continue
            for k, (eid, fields, reclaimed) in enumerate(entries):
                if k:
                    await slots.acquire()
                t = asyncio.create_task(one(eid, fields, reclaimed))
                active.add(t)
                t.add_done_callback(active.discard)
    finally:
        for t in list(active):
            t.cancel()
