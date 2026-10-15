"""The plant simulator. One tick per telemetry interval (real seconds); each tick advances the plant clock.

Per tick it: advances hidden faults (wear grows faster at higher line speed), injects random faults, decides machine
and line states, produces sensor readings (live stream + historian), raises/clears OT alarms, runs production against
the orders (MES), records quality results (QMS), moves stock transfers (ERP), finishes repair verifications and keeps
the shift rota / schedule topped up. Everything it writes is linked: defects name the machine and order, transfers
move stock with stock movements, a repair consumes parts from the plant that did it, and so on.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator

from pydantic import BaseModel

from plantguard_contracts.schemas.common import DataQuality, MachineState
from plantguard_contracts.schemas.ot import TelemetryMessage

from app.sim.clock import SimClock, iso, parse_ts
from app.sim.db import Database
from app.sim.physics import SIGNALS, Condition, readings
from app.sim.seed import ensure_schedule, ensure_shifts
from app.sim.settings import SimSettings

log = logging.getLogger("plantguard.sim")


class RepairVerified(BaseModel):
    incident_id: str
    work_order_id: str
    asset_id: str
    passed: bool
    max_risk: float | None = None
    details: str = ""
    ts: datetime


class OtAlarm(BaseModel):
    alarm_id: str
    asset_id: str
    code: str
    severity: str
    message: str
    ts: datetime


class TelemetryBatch(BaseModel):
    """One tick of one line's readings, published on `ot.raw.<plant>.<line>` for the other processes."""
    plant_id: str
    line_id: str
    messages: list[TelemetryMessage]


class FaultStarted(BaseModel):
    asset_id: str
    ts: datetime


@dataclass
class AssetRt:
    asset_id: str
    plant_id: str
    line_id: str
    type: str
    baseline: dict
    alarm: dict
    defect: dict
    station_seq: int


class PlantEngine:
    def __init__(self, cfg: SimSettings, ot: Database, it: Database, clock: SimClock, bus, rng: random.Random | None = None):
        self.cfg, self.ot, self.it, self.clock, self.bus = cfg, ot, it, clock, bus
        self.rng = rng or random.Random()
        self.models, self.sigs = cfg.plant["models"], cfg.plant["signatures"]
        self.assets: dict[str, AssetRt] = {}
        for r in ot.q("SELECT asset_id, site_id, line_id, type, station_id FROM equipment ORDER BY asset_id"):
            m = self.models[r["type"]]
            seq = ot.val("SELECT seq FROM stations WHERE site_id=? AND id=?", (r["site_id"], r["station_id"]), 1)
            self.assets[r["asset_id"]] = AssetRt(r["asset_id"], r["site_id"], r["line_id"], r["type"], m["baseline"],
                                                 m["alarm"], m["defect"], seq)
        self.latest: dict[str, TelemetryMessage] = {}
        self._subs: dict[tuple[str, str], set[asyncio.Queue]] = defaultdict(set)
        self._last_hist: datetime | None = None
        self._smooth: dict[str, dict[str, float]] = {}
        self._carry: dict[tuple[str, str], float] = defaultdict(float)  # fractional units per line
        self._last_tick: datetime | None = None
        self._ticks = 0
        self.health = None  # set by the container: HealthProvider for verification
        self.running = False
        # several processes: the engine process publishes each tick's readings on the shared bus and keeps the last
        # values in Redis; API processes (which do not run the loop) stream from there
        self.shared_kv = None
        self.share_telemetry = False
        self.drives_plant = True  # False in API / worker processes: they never run the loop

    # ------------------------------------------------------------------ public helpers
    def line_of(self, asset_id: str) -> tuple[str, str]:
        a = self.assets[asset_id]
        return a.plant_id, a.line_id

    def line_speed(self, plant_id: str, line_id: str, at: datetime | None = None) -> int:
        t = iso(at or self.clock())
        v = self.it.val("SELECT speed_pct FROM line_speed_plan WHERE plant_id=? AND line_id=? AND start_ts<=? AND "
                        "(end_ts IS NULL OR end_ts>?) ORDER BY id DESC LIMIT 1", (plant_id, line_id, t, t))
        return int(v) if v is not None else 100

    def machine_state(self, asset_id: str) -> tuple[str, str | None]:
        r = self.ot.one("SELECT state, reason FROM machine_state WHERE asset_id=?", (asset_id,))
        return (r["state"], r["reason"]) if r else ("run", None)

    def condition(self, asset_id: str):
        return self.ot.one("SELECT * FROM asset_conditions WHERE asset_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
                           (asset_id,))

    def line_running(self, plant_id: str, line_id: str) -> bool:
        down = self.ot.val("SELECT COUNT(*) FROM machine_state m JOIN equipment e ON e.asset_id=m.asset_id "
                           "WHERE e.site_id=? AND e.line_id=? AND m.state IN ('fault','stopped')", (plant_id, line_id), 0)
        return down == 0 and self.line_speed(plant_id, line_id) > 0

    async def subscribe(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]:
        """Live readings of one line (TelemetrySource for the OT gateway)."""
        if self.share_telemetry and not self.drives_plant:
            async for m in self._subscribe_shared(plant_id, line_id):
                yield m
            return
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs[(plant_id, line_id)].add(q)
        try:
            for a in self.assets.values():  # last known values first, so a new viewer is not empty
                if a.plant_id == plant_id and a.line_id == line_id and a.asset_id in self.latest:
                    yield self.latest[a.asset_id]
            while True:
                yield await q.get()
        finally:
            self._subs[(plant_id, line_id)].discard(q)

    async def _subscribe_shared(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]:
        sub = self.bus.subscribe(f"ot.raw.{plant_id}.{line_id}")
        try:
            await sub.ready()
            if self.shared_kv is not None:
                for raw in self.shared_kv.all_json(f"telemetry:{plant_id}:{line_id}").values():
                    yield TelemetryMessage.model_validate(raw)
            async for payload in sub:
                for m in payload.get("messages", ()):
                    yield TelemetryMessage.model_validate(m)
        finally:
            sub.close()

    async def _share(self, batches: dict[tuple[str, str], list[TelemetryMessage]]) -> None:
        for (plant_id, line_id), msgs in batches.items():
            await self.bus.publish(f"ot.raw.{plant_id}.{line_id}", TelemetryBatch(plant_id=plant_id, line_id=line_id,
                                                                               messages=msgs))
            if self.shared_kv is not None:
                kv, name = self.shared_kv, f"telemetry:{plant_id}:{line_id}"
                values = {m.asset_id: m.model_dump(mode="json") for m in msgs}
                await asyncio.to_thread(kv.put_many_json, name, values)

    def set_state(self, asset_id: str, state: str, reason: str | None) -> None:
        now = iso(self.clock())
        cur = self.ot.one("SELECT state, reason FROM machine_state WHERE asset_id=?", (asset_id,))
        if cur and cur["state"] == state and cur["reason"] == reason:
            return
        with self.ot.tx() as c:
            c.execute("UPDATE machine_state_log SET end_ts=? WHERE asset_id=? AND end_ts IS NULL", (now, asset_id))
            c.execute("INSERT INTO machine_state_log(asset_id, state, reason, start_ts) VALUES(?,?,?,?)",
                      (asset_id, state, reason, now))
            c.execute("UPDATE machine_state SET state=?, reason=?, since=? WHERE asset_id=?", (state, reason, now, asset_id))

    # ------------------------------------------------------------------ faults
    def inject_fault(self, asset_id: str | None = None, mode: str | None = None, progress: float = 0.02,
                     variant: str | None = None) -> dict | None:
        """Start a fault (random machine/mode unless given). Returns the new condition row or None."""
        now = self.clock()
        if asset_id is None:
            busy = {r["asset_id"] for r in self.ot.q("SELECT asset_id FROM asset_conditions WHERE ended_at IS NULL")}
            free = [a for a in self.assets if a not in busy and self.machine_state(a)[0] == "run"]
            if not free:
                return None
            asset_id = self.rng.choice(free)
        elif self.condition(asset_id) is not None:
            return None
        a = self.assets[asset_id]
        modes = self.models[a.type]["modes"]
        mode = mode or self.rng.choice(list(modes))
        spec = modes[mode]
        if variant is None:
            variant = mode
            if mode == "misalignment" and self.rng.random() < self.cfg.misalignment_looks_like_bearing:
                variant = "misalignment_loose"
        T = self.rng.uniform(self.cfg.breakdown_h_min, self.cfg.breakdown_h_max)
        self.ot.x("INSERT INTO asset_conditions(asset_id, fault_mode, variant, root_cause, fix_part, progress, "
                  "breakdown_h, started_at) VALUES(?,?,?,?,?,?,?,?)",
                  (asset_id, mode, variant, spec["root_cause"], spec["part"], progress, T, iso(now)))
        log.info("fault started on %s: %s (%s), breakdown in ~%.0f plant h", asset_id, mode, variant, T * (1 - progress))
        return dict(self.condition(asset_id))

    def apply_repair(self, asset_id: str, parts: list[str]) -> tuple[bool, str]:
        """Physics of a finished repair. The fault is gone only if the work replaced the part that actually failed
        (and the job was done right). Returns (fault cleared, explanation for the record)."""
        now = iso(self.clock())
        c = self.condition(asset_id)
        if c is None:
            return True, "No developing fault found; machine checked and restarted."
        if c["fix_part"] in parts:
            if self.rng.random() < self.cfg.bad_install_probability:
                self.ot.x("UPDATE asset_conditions SET ended_at=?, end_reason='repaired' WHERE id=?", (now, c["id"]))
                self.ot.x("INSERT INTO asset_conditions(asset_id, fault_mode, variant, root_cause, fix_part, progress, "
                          "breakdown_h, started_at) VALUES(?,?,?,?,?,?,?,?)",
                          (asset_id, c["fault_mode"], c["variant"], c["root_cause"] + " (after a faulty installation)",
                           c["fix_part"], 0.4, c["breakdown_h"] * 0.6, now))
                return False, "Part replaced but the installation was not right; the fault is still developing."
            self.ot.x("UPDATE asset_conditions SET ended_at=?, end_reason='repaired' WHERE id=?", (now, c["id"]))
            return True, f"Real cause fixed: {c['root_cause']}."
        return False, "The replaced part was not the one failing; the real cause is still there."

    # ------------------------------------------------------------------ main loop
    async def run(self) -> None:
        self.running = True
        interval = self.cfg.telemetry_interval_s
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.tick()
            except Exception:  # one bad tick must not stop the plant
                log.exception("simulation tick failed")
            await asyncio.sleep(max(0.05, interval - (asyncio.get_running_loop().time() - started)))

    async def tick(self) -> None:
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
            return
        dt_h = max(0.0, (now - self._last_tick).total_seconds() / 3600)
        self._last_tick = now
        if dt_h <= 0:
            return
        self._ticks += 1
        conds = {r["asset_id"]: r for r in self.ot.q("SELECT * FROM asset_conditions WHERE ended_at IS NULL")}
        speeds = {(a.plant_id, a.line_id): None for a in self.assets.values()}
        for k in speeds:
            speeds[k] = self.line_speed(*k, at=now)

        # 1. faults progress (only while the machine runs; slower at reduced speed)
        for aid, c in conds.items():
            a = self.assets[aid]
            state, _ = self.machine_state(aid)
            if state != "run":
                continue
            sf = (speeds[(a.plant_id, a.line_id)] or 0) / 100
            p = min(1.0, c["progress"] + dt_h / c["breakdown_h"] * sf ** self.cfg.speed_exponent)
            self.ot.x("UPDATE asset_conditions SET progress=? WHERE id=?", (p, c["id"]))
            if p >= 1.0:
                self.set_state(aid, "fault", "breakdown")
                await self._raise(aid, "MCH-FLT", f"{aid} stopped on fault ({c['fault_mode'].replace('_', ' ')})", None)
        conds = {r["asset_id"]: r for r in self.ot.q("SELECT * FROM asset_conditions WHERE ended_at IS NULL")}

        # 2. random faults (Poisson process in plant time)
        if self.cfg.random_faults and len(conds) < self.cfg.max_concurrent_faults:
            if self.rng.random() < 1 - math.exp(-dt_h / max(0.1, self.cfg.mean_fault_interval_h)):
                c = self.inject_fault()
                if c:
                    await self.bus.publish("sim.fault", FaultStarted(asset_id=c["asset_id"], ts=now))

        # 3. machine states (a stopped machine idles the rest of its line)
        line_up = {k: self.line_running(*k) for k in speeds}
        for aid, a in self.assets.items():
            state, reason = self.machine_state(aid)
            if state in ("fault", "stopped"):
                continue
            want = "run" if line_up[(a.plant_id, a.line_id)] else "idle"
            if state != want:
                self.set_state(aid, want, "production" if want == "run" else "line stopped")

        # 4. readings, live stream, alarms
        write_hist = self._last_hist is None or (now - self._last_hist) >= timedelta(minutes=self.cfg.history_interval_min)
        hist_rows = []
        batches: dict[tuple[str, str], list[TelemetryMessage]] = defaultdict(list)
        for aid, a in self.assets.items():
            state, _ = self.machine_state(aid)
            c = conds.get(aid)
            cond = Condition(c["fault_mode"], c["variant"], c["progress"]) if c else None
            v = readings(a.baseline, self.sigs, cond, speeds[(a.plant_id, a.line_id)], state, self.rng, self.cfg.noise)
            msg = TelemetryMessage(asset_id=aid, line_id=a.line_id, ts=now,
                                   state=MachineState(state) if state in MachineState._value2member_map_ else MachineState.STOPPED,
                                   values=v, quality=DataQuality.GOOD)
            self.latest[aid] = msg
            batches[(a.plant_id, a.line_id)].append(msg)
            for q in list(self._subs.get((a.plant_id, a.line_id), ())):
                if q.full():
                    q.get_nowait()
                q.put_nowait(msg)
            if write_hist:
                hist_rows.append((aid, iso(now), state, speeds[(a.plant_id, a.line_id)], *[v[s] for s in SIGNALS], "good"))
            await self._alarms(aid, a, v, state)
        if self.share_telemetry:
            try:
                await self._share(batches)
            except Exception:
                log.exception("publishing telemetry failed")
        if hist_rows:
            self.ot.many("INSERT INTO historian(asset_id, ts, state, speed_pct, vibration_rms, vibration_peak, "
                         "vibration_kurtosis, bearing_temp, motor_temp, spindle_rpm, motor_current, power_kw, quality) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id, ts) DO NOTHING", hist_rows)
            self._last_hist = now

        # 5. production, quality
        self._production(now, dt_h, speeds, line_up, conds)
        # 6. logistics
        self._transfers(now)
        # 7. repair verifications
        await self._verifications(now)
        # 8. housekeeping
        if self._ticks % 30 == 0:
            self.it.set_meta("plant_time", iso(now))
            ensure_shifts(self.it, self.cfg, now - timedelta(days=1), 4)
            ensure_schedule(self.it, self.cfg, now)
        if self._ticks % 300 == 0:
            cutoff = iso(now - timedelta(hours=self.cfg.history_retention_h))
            self.ot.x("DELETE FROM historian WHERE ts < ?", (cutoff,))

    # ------------------------------------------------------------------ alarms
    async def _alarms(self, aid: str, a: AssetRt, v: dict, state: str) -> None:
        sm = self._smooth.setdefault(aid, {})
        for k in ("vibration_rms", "motor_temp"):
            sm[k] = v[k] if k not in sm else 0.6 * sm[k] + 0.4 * v[k]
        if state != "run":
            return
        checks = [("VIB-HI-C", sm["vibration_rms"], a.alarm["vibration_crit"], "mm/s"),
                  ("VIB-HI-W", sm["vibration_rms"], a.alarm["vibration_warn"], "mm/s"),
                  ("TMP-HI-C", sm["motor_temp"], a.alarm["temp_crit"], "°C"),
                  ("TMP-HI-W", sm["motor_temp"], a.alarm["temp_warn"], "°C")]
        for code, value, limit, unit in checks:
            active = self.ot.one("SELECT id FROM alarms WHERE asset_id=? AND code=? AND cleared_at IS NULL", (aid, code))
            if value >= limit and not active:
                text = self.ot.val("SELECT text FROM alarm_defs WHERE code=?", (code,))
                await self._raise(aid, code, f"{text}: {value:.1f} {unit} (limit {limit} {unit})", value)
            elif value < limit * 0.95 and active:
                self.ot.x("UPDATE alarms SET cleared_at=? WHERE id=?", (iso(self.clock()), active["id"]))

    async def _raise(self, aid: str, code: str, message: str, value: float | None) -> None:
        if self.ot.one("SELECT 1 FROM alarms WHERE asset_id=? AND code=? AND cleared_at IS NULL", (aid, code)):
            return
        sev = self.ot.val("SELECT severity FROM alarm_defs WHERE code=?", (code,))
        alarm_id = f"ALM-{self.it.next_id('ALM', 9001)}"
        now = self.clock()
        self.ot.x("INSERT INTO alarms(id, asset_id, code, severity, message, value, raised_at) VALUES(?,?,?,?,?,?,?)",
                  (alarm_id, aid, code, sev, message, value, iso(now)))
        await self.bus.publish("ot.alarm", OtAlarm(alarm_id=alarm_id, asset_id=aid, code=code, severity=sev,
                                                   message=message, ts=now))

    def clear_alarms(self, asset_id: str) -> None:
        self.ot.x("UPDATE alarms SET cleared_at=? WHERE asset_id=? AND cleared_at IS NULL", (iso(self.clock()), asset_id))

    # ------------------------------------------------------------------ MES / QMS
    def _production(self, now: datetime, dt_h: float, speeds, line_up, conds) -> None:
        hour = iso(now.replace(minute=0, second=0, microsecond=0))
        for (plant_id, line_id), speed in speeds.items():
            order = self.it.one("SELECT * FROM production_orders WHERE plant_id=? AND line_id=? AND status!='completed' "
                                "ORDER BY seq LIMIT 1", (plant_id, line_id))
            running = line_up[(plant_id, line_id)]
            units = self.cfg.line_rate_units_per_h * (speed / 100) * dt_h if running and order else 0.0
            defects = 0
            if order is not None:
                if order["status"] == "planned":
                    self.it.x("UPDATE production_orders SET status='running', started_at=? WHERE id=?", (iso(now), order["id"]))
                key = (plant_id, line_id)
                self._carry[key] += units
                whole = int(self._carry[key])
                self._carry[key] -= whole
                done = min(order["quantity"], order["completed"] + whole)
                left = order["quantity"] - done
                due = parse_ts(order["due_at"])
                need_h = left / self.cfg.line_rate_units_per_h
                status = "completed" if left <= 0 else ("at_risk" if now + timedelta(hours=need_h) > due else "running")
                self.it.x("UPDATE production_orders SET completed=?, status=?, completed_at=? WHERE id=?",
                          (done, status, iso(now) if status == "completed" else None, order["id"]))
                defects = self._quality(now, plant_id, line_id, order["id"], units, conds)
            self.it.x("INSERT INTO production_log(plant_id, line_id, hour_ts, order_id, planned_min, run_min, speed_sum, "
                      "units, defects) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(plant_id, line_id, hour_ts) DO UPDATE SET "
                      "planned_min=production_log.planned_min+excluded.planned_min, run_min=production_log.run_min+excluded.run_min, "
                      "speed_sum=production_log.speed_sum+excluded.speed_sum, units=production_log.units+excluded.units, "
                      "defects=production_log.defects+excluded.defects",
                      (plant_id, line_id, hour, order["id"] if order else None, dt_h * 60, dt_h * 60 if running else 0,
                       speed * dt_h * 60 if running else 0, units, defects))

    def _quality(self, now: datetime, plant_id: str, line_id: str, order_id: str, units: float, conds) -> int:
        """Inspection results: a developing fault on the line raises the defect rate of the parts it makes."""
        if units <= 0:
            return 0
        n = 0
        inspected = units * self.cfg.inspection_share
        for a in self.assets.values():
            if a.plant_id != plant_id or a.line_id != line_id:
                continue
            c = conds.get(a.asset_id)
            p = c["progress"] if c else 0.0
            rate = self.cfg.base_defect_rate / 4 + max(0.0, p - 0.25) * 0.08
            expected = inspected * rate
            k = int(expected) + (1 if self.rng.random() < expected - int(expected) else 0)
            d = a.defect
            for _ in range(k):
                span = d["spec_max"] - d["spec_min"]
                measured = d["spec_max"] + span * self.rng.uniform(0.02, 0.4) if self.rng.random() < 0.8 else \
                    d["spec_min"] - span * self.rng.uniform(0.02, 0.4)
                self.it.x("INSERT INTO defects(id, plant_id, line_id, asset_id, order_id, defect_type, measured, spec_min, "
                          "spec_max, ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (f"DEF-{self.it.next_id('DEF', 800)}", plant_id, line_id, a.asset_id, order_id, d["type"],
                           round(measured, 4), d["spec_min"], d["spec_max"], iso(now)))
                n += 1
        if self.rng.random() < min(1.0, inspected / 5):
            self.it.x("INSERT INTO inspections(id, plant_id, line_id, lot_id, order_id, passed, ts) VALUES(?,?,?,?,?,?,?)",
                      (f"INS-{self.it.next_id('INS', 5000)}", plant_id, line_id, f"LOT-{order_id[-4:]}-{now:%d%H}", order_id,
                       int(n == 0), iso(now)))
        return n

    # ------------------------------------------------------------------ ERP logistics
    def _transfers(self, now: datetime) -> None:
        t = iso(now)
        for tr in self.it.q("SELECT * FROM stock_transfers WHERE status='created' AND departs_at<=?", (t,)):
            with self.it.tx() as c:
                left = c.execute("SELECT quantity FROM stock WHERE sku=? AND plant_id=?", (tr["sku"], tr["from_plant"])).fetchone()
                if left is None or left["quantity"] < tr["quantity"]:
                    continue  # nothing to ship yet
                c.execute("UPDATE stock SET quantity=quantity-? WHERE sku=? AND plant_id=?",
                          (tr["quantity"], tr["sku"], tr["from_plant"]))
                c.execute("INSERT INTO stock_movements(sku, plant_id, qty_delta, reason, transfer_id, ts) VALUES(?,?,?,?,?,?)",
                          (tr["sku"], tr["from_plant"], -tr["quantity"], "transfer_out", tr["id"], t))
                c.execute("UPDATE stock_transfers SET status='in_transit' WHERE id=?", (tr["id"],))
        for tr in self.it.q("SELECT * FROM stock_transfers WHERE status='in_transit' AND eta<=?", (t,)):
            with self.it.tx() as c:
                loc = c.execute("SELECT id FROM storage_locations WHERE plant_id=?", (tr["to_plant"],)).fetchone()["id"]
                c.execute("INSERT INTO stock(sku, plant_id, location_id, quantity) VALUES(?,?,?,?) ON CONFLICT(sku, plant_id) "
                          "DO UPDATE SET quantity=stock.quantity+excluded.quantity", (tr["sku"], tr["to_plant"], loc, tr["quantity"]))
                c.execute("INSERT INTO stock_movements(sku, plant_id, qty_delta, reason, transfer_id, ts) VALUES(?,?,?,?,?,?)",
                          (tr["sku"], tr["to_plant"], tr["quantity"], "transfer_in", tr["id"], t))
                c.execute("UPDATE stock_transfers SET status='delivered', delivered_at=? WHERE id=?", (t, tr["id"]))

        for po in self.it.q("SELECT * FROM purchase_orders WHERE status='ordered' AND eta<=?", (t,)):
            with self.it.tx() as c:
                loc = c.execute("SELECT id FROM storage_locations WHERE plant_id=?", (po["to_plant"],)).fetchone()["id"]
                c.execute("INSERT INTO stock(sku, plant_id, location_id, quantity) VALUES(?,?,?,?) ON CONFLICT(sku, plant_id) "
                          "DO UPDATE SET quantity=stock.quantity+excluded.quantity", (po["sku"], po["to_plant"], loc, po["quantity"]))
                c.execute("INSERT INTO stock_movements(sku, plant_id, qty_delta, reason, purchase_order_id, ts) "
                          "VALUES(?,?,?,?,?,?)", (po["sku"], po["to_plant"], po["quantity"], "purchase_receipt", po["id"], t))
                c.execute("UPDATE purchase_orders SET status='delivered', delivered_at=? WHERE id=?", (t, po["id"]))
            log.info("goods receipt %s: %d× %s at %s", po["id"], po["quantity"], po["sku"], po["to_plant"])

    # ------------------------------------------------------------------ verification after a repair
    def start_verification(self, incident_id: str, work_order_id: str) -> str:
        now = self.clock()
        ends = now + timedelta(minutes=self.cfg.verify_min)
        self.it.x("INSERT INTO repair_verifications(incident_id, work_order_id, started_at, ends_at, result) "
                  "VALUES(?,?,?,?, 'pending')", (incident_id, work_order_id, iso(now), iso(ends)))
        return iso(ends)

    async def _verifications(self, now: datetime) -> None:
        for v in self.it.q("SELECT v.*, w.asset_id FROM repair_verifications v JOIN work_orders w ON w.id=v.work_order_id "
                           "WHERE v.result='pending' AND v.ends_at<=?", (iso(now),)):
            aid = v["asset_id"]
            risk = None
            # judge the repair only on readings taken after the restart: at a fast plant clock the check window can be
            # shorter than the scorer's window, and the cached score would still be the pre-repair one
            fresh = None
            try:
                if self.health is not None and hasattr(self.health, "health_since"):
                    fresh = self.health.health_since(aid, parse_ts(v["started_at"]))
                elif self.health is not None:
                    fresh = self.health.get_health(aid)
            except Exception:
                log.exception("repair check: scoring %s failed", aid)
            state_now, _ = self.machine_state(aid)
            if fresh is None and state_now == "run" and now < parse_ts(v["ends_at"]) + timedelta(minutes=4 * self.cfg.verify_min):
                continue  # not enough post-restart readings yet: keep watching
            risk = fresh.risk_72h if fresh is not None else None
            alarms = self.ot.val("SELECT COUNT(*) FROM alarms WHERE asset_id=? AND cleared_at IS NULL", (aid,), 0)
            state, _ = self.machine_state(aid)
            passed = state == "run" and alarms == 0 and (risk is None or risk <= self.cfg.verify_max_risk)
            details = (f"Readings normal for {self.cfg.verify_min:.0f} min after restart (risk {risk:.0%})" if passed else
                       f"Still abnormal after the repair: risk {risk:.0%}" if risk is not None else "Still abnormal") \
                if risk is not None else ("Machine running normally" if passed else "Machine not running normally")
            if state == "fault":
                details = "Machine broke down again during the check"
            elif alarms and not passed:
                details += f", {alarms} active alarm{'s' if alarms > 1 else ''}"
            self.it.x("UPDATE repair_verifications SET result=?, max_risk=?, details=? WHERE id=?",
                      ("passed" if passed else "failed", risk, details, v["id"]))
            await self.bus.publish("sim.verification", RepairVerified(
                incident_id=v["incident_id"], work_order_id=v["work_order_id"], asset_id=aid, passed=passed,
                max_risk=risk, details=details, ts=now))
