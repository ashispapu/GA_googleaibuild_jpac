"""Restore a simulated plant without deleting identity, manuals or incident/audit history.

The API requests a reset; the simulation leader performs it between ticks, under the same lock as scoring.
Per-plant generations stop old worker runs and timestamps discard pre-reset telemetry/alerts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import timedelta

from fastapi import HTTPException

from plantguard_contracts.schemas.common import MachineState
from plantguard_contracts.schemas.incidents import IncidentStatus
from plantguard_contracts.schemas.ot import TelemetryMessage

from app.sim.clock import iso
from app.sim.physics import SIGNALS, readings

log = logging.getLogger("plantguard.reset")


class PlantReset:
    def __init__(self, engine, scorer, scores, incidents, garden, audit):
        self.engine, self.scorer, self.scores = engine, scorer, scores
        self.incidents, self.garden, self.audit = incidents, garden, audit
        engine.reset_handler = self.process_pending

    async def request(self, plant_id: str, actor: str, timeout_s: float = 30) -> None:
        it = self.engine.it
        request = {"id": uuid.uuid4().hex, "plant_id": plant_id, "actor": actor, "at": time.time()}
        with it.tx() as conn:
            it.advisory_lock(conn, "plantguard-reset-request")
            pending = json.loads(it.meta("sim_reset_request", "null"))
            if pending and time.time() - pending["at"] < 60:
                raise HTTPException(409, "A plant reset is already in progress")
            it.set_meta("sim_reset_request", json.dumps(request))
        if self.engine.drives_plant:
            async with self.engine.operation_lock:
                await self.process_pending()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            result = json.loads(it.meta("sim_reset_result", "null"))
            if result and result["id"] == request["id"]:
                if not result["ok"]:
                    raise HTTPException(503, "Could not restore the plant; check the engine logs")
                # Drop this API process's one-second score cache; read the leader's fresh results.
                if not self.engine.drives_plant:
                    self.scores.invalidate([a.asset_id for a in self.engine.assets.values() if a.plant_id == plant_id])
                self.engine._bd_cache = (0.0, False)
                return
            await asyncio.sleep(0.1)
        raise HTTPException(503, "The simulation engine has not completed the reset; try again when it is running")

    async def process_pending(self) -> bool:
        it = self.engine.it
        request = json.loads(it.meta("sim_reset_request", "null"))
        if not request:
            return False
        try:
            await self.apply(request["plant_id"], request["actor"], request["id"])
        except Exception:
            log.exception("plant reset failed")
            it.set_meta("sim_reset_result", json.dumps({"id": request["id"], "ok": False}))
        else:
            it.set_meta("sim_reset_result", json.dumps({"id": request["id"], "ok": True}))
        # Clear only our request, not a newer request submitted after a timeout.
        if json.loads(it.meta("sim_reset_request", "null")) == request:
            it.set_meta("sim_reset_request", "null")
        return True

    async def apply(self, plant_id: str, actor: str, generation: str) -> None:
        e, it, ot = self.engine, self.engine.it, self.engine.ot
        assets = [a for a in e.assets.values() if a.plant_id == plant_id]
        if not assets:
            raise ValueError("Unknown plant")
        now = e.clock()
        stamp = iso(now)
        records = [r for r in self.incidents.repo.all() if r.incident.plant_id == plant_id]
        closed = []
        with it.tx():
            it.set_meta(f"sim_reset_generation:{plant_id}", generation)
            it.set_meta(f"sim_reset_at:{plant_id}", now.isoformat())
            e.set_breakdowns(False)
            if any(a.asset_id in e.cfg.initial_faults for a in assets):
                it.set_meta("story_faults_pending", "1")  # the next deliberate switch-on starts a new story
            for rec in records:
                inc = rec.incident
                if inc.status not in (IncidentStatus.RESOLVED, IncidentStatus.REJECTED):
                    inc.status = IncidentStatus.RESOLVED
                    inc.resolved_at = inc.updated_at = now
                    self.incidents.repo.save(rec)
                    closed.append(rec)
            it.x("UPDATE work_orders SET status='done', completed_at=?, outcome='simulator_reset', "
                 "findings=COALESCE(findings, '') || ' Closed by simulation reset, not a technician repair.' "
                 "WHERE asset_id IN (SELECT asset_id FROM equipment WHERE plant_id=?) AND status!='done'",
                 (stamp, plant_id))
            it.x("UPDATE repair_verifications SET result='failed', details='Cancelled by simulation reset' "
                 "WHERE incident_id IN (SELECT id FROM incidents WHERE plant_id=?) AND result='pending'", (plant_id,))
            it.x("UPDATE line_speed_plan SET end_ts=? WHERE plant_id=? AND (end_ts IS NULL OR end_ts>?)",
                 (stamp, plant_id, stamp))
            it.x("DELETE FROM schedule_slots WHERE plant_id=? AND kind!='production' AND end_ts>?", (plant_id, stamp))
        ids = [a.asset_id for a in assets]
        marks = ",".join("?" for _ in ids)
        with ot.tx():
            ot.x(f"UPDATE asset_conditions SET ended_at=?, end_reason='simulator_reset' "
                 f"WHERE asset_id IN ({marks}) AND ended_at IS NULL", (stamp, *ids))
            ot.x(f"UPDATE alarms SET cleared_at=?, acknowledged=1 WHERE asset_id IN ({marks}) AND cleared_at IS NULL",
                 (stamp, *ids))
            ot.x(f"DELETE FROM historian WHERE asset_id IN ({marks})", ids)
            ot.x(f"UPDATE machine_state_log SET end_ts=? WHERE asset_id IN ({marks}) AND end_ts IS NULL", (stamp, *ids))
            ot.many("INSERT INTO machine_state_log(asset_id, state, reason, start_ts) VALUES(?,?,?,?)",
                    [(aid, "run", "production", stamp) for aid in ids])
            ot.x(f"UPDATE machine_state SET state='run', reason='production', since=? WHERE asset_id IN ({marks})",
                 (stamp, *ids))
        self.scorer.reset_assets(ids, now)
        batches = defaultdict(list)
        rows = []
        for a in assets:
            e._smooth.pop(a.asset_id, None)
            values = readings(a.baseline, e.sigs, None, 100, "run", e.rng, 0)
            # Healthy backfill gives charts and the model real baseline values immediately, not a fake UI color.
            messages = [TelemetryMessage(asset_id=a.asset_id, line_id=a.line_id, ts=now-timedelta(minutes=i),
                                         state=MachineState.RUN, values=values) for i in range(12, -1, -1)]
            for msg in messages:
                rows.append((a.asset_id, iso(msg.ts), "run", 100, *[values[s] for s in SIGNALS], "good"))
            score = self.scorer._model.score(self.scorer._extractor.extract(a.asset_id, messages, 0))
            self.scores.put(score)
            e.latest[a.asset_id] = messages[-1]
            batches[(a.plant_id, a.line_id)].append(messages[-1])
        ot.many("INSERT INTO historian(asset_id, ts, state, speed_pct, vibration_rms, vibration_peak, "
                "vibration_kurtosis, bearing_temp, motor_temp, spindle_rpm, motor_current, power_kw, quality) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id, ts) DO NOTHING", rows)
        for rec in records:
            self.garden.agents.pop(rec.incident.id, None)
            if self.garden.kv is not None:
                self.garden.kv.delete("garden", rec.incident.id)
        # Every background status/approval handler sees resolved records; stale queued runs are skipped.
        for rec in closed:
            await e.bus.publish(f"incident.{rec.incident.id}.status", rec.incident)
        if e.share_telemetry:
            await e._share(batches)
        for key, messages in batches.items():
            for queue in e._subs.get(key, ()):
                while not queue.empty():
                    queue.get_nowait()
                for msg in messages:
                    if not queue.full():
                        queue.put_nowait(msg)
        e._last_hist = None
        e._last_tick = now
        e.clock.set_scale(e.cfg.scale)
        self.audit.log("user", actor, "reset_plant", plant_id,
                       {"machines": len(assets), "resolved_incidents": len(closed), "generation": generation})
