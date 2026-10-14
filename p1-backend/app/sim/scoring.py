"""Health scoring for the simulated plant: live readings -> features -> model -> ml.health.{asset} and alerts.raised.
Only readings taken while the machine runs are scored; the window restarts whenever the machine stops or restarts
(so a repaired machine is judged on fresh data)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta

from plantguard_contracts.schemas.common import MachineState
from plantguard_contracts.schemas.ml import AssetHealth, FeatureWindow, ModelInfo
from plantguard_contracts.schemas.ot import TelemetryMessage

from app.connectors.health import LatestScores
from app.services.scoring import HealthScorer
from app.sim.clock import iso, parse_ts
from app.sim.db import Database
from app.sim.health_model import SignatureFeatureExtractor, SignatureHealthModel

log = logging.getLogger("plantguard.sim.scoring")
SIGNAL_COLS = ("vibration_rms", "vibration_peak", "vibration_kurtosis", "bearing_temp", "motor_temp", "spindle_rpm",
               "motor_current", "power_kw")


def historian_past(ot: Database):
    def past(asset_id: str, end: datetime, hours_ago: float) -> dict[str, float] | None:
        t = end - timedelta(hours=hours_ago)
        r = ot.one(f"SELECT {', '.join(f'AVG({c}) AS {c}' for c in SIGNAL_COLS)}, COUNT(*) AS n FROM historian "
                   "WHERE asset_id=? AND state='run' AND ts BETWEEN ? AND ?",
                   (asset_id, iso(t - timedelta(minutes=15)), iso(t + timedelta(minutes=15))))
        return {c: r[c] for c in SIGNAL_COLS} if r and r["n"] else None
    return past


def hours_since_service(it: Database, clock):
    def hours(asset_id: str) -> float:
        r = it.one("SELECT COALESCE(completed_at, planned_start) AS t FROM work_orders WHERE asset_id=? AND status='done' "
                   "ORDER BY COALESCE(completed_at, planned_start) DESC LIMIT 1", (asset_id,))
        return max(0.0, (clock() - parse_ts(r["t"])).total_seconds() / 3600) if r and r["t"] else 4000.0
    return hours


class RunningOnlyScorer(HealthScorer):
    """Scores the last `keep` readings taken while the machine ran. Count-based (not a fixed 10 plant minutes), so
    detection works at any plant-clock speed; readings older than `max_age` are dropped."""

    def __init__(self, *a, keep: int = 12, max_age: timedelta = timedelta(hours=1), **kw):
        super().__init__(*a, **kw)
        self._state: dict[str, str] = {}
        self._keep, self._max_age = keep, max_age

    def ingest(self, msg: TelemetryMessage) -> None:
        prev = self._state.get(msg.asset_id)
        self._state[msg.asset_id] = msg.state.value
        if msg.state != MachineState.RUN or (prev is not None and prev != MachineState.RUN.value):
            self._buffers.pop(msg.asset_id, None)  # stopped, or restarted: start a fresh window
            if msg.state != MachineState.RUN:
                return
        if msg.quality.value != "good":
            return
        buf = self._buffers.setdefault(msg.asset_id, deque(maxlen=self._keep))
        if not isinstance(buf, deque) or buf.maxlen != self._keep:
            buf = self._buffers[msg.asset_id] = deque(buf, maxlen=self._keep)
        buf.append(msg)
        while buf and msg.ts - buf[0].ts > self._max_age:
            buf.popleft()


class SimScoringService:
    def __init__(self, engine, scorer: RunningOnlyScorer, interval_s: float):
        self.engine, self.scorer, self.interval = engine, scorer, interval_s

    async def run(self) -> None:
        lines = sorted({(a.plant_id, a.line_id) for a in self.engine.assets.values()})
        tasks = [asyncio.create_task(self._ingest(p, l)) for p, l in lines] + [asyncio.create_task(self._loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def _ingest(self, plant_id: str, line_id: str) -> None:
        async for msg in self.engine.subscribe(plant_id, line_id):
            self.scorer.ingest(msg)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.scorer.evaluate()
            except Exception:
                log.exception("scoring cycle failed")


class SimHealthProvider:  # implements HealthProvider
    """Latest live score; before the first live score, the model scores the historian's last window."""

    def __init__(self, model: SignatureHealthModel, extractor: SignatureFeatureExtractor, scores: LatestScores,
                 ot: Database, hours, clock, window_min: float):
        self.model, self.extractor, self.scores, self.ot = model, extractor, scores, ot
        self.hours, self.clock, self.window_min = hours, clock, window_min

    def get_health(self, asset_id: str) -> AssetHealth:
        if not self.ot.one("SELECT 1 FROM equipment WHERE asset_id=?", (asset_id,)):
            raise KeyError(asset_id)
        h = self.scores.get(asset_id)
        if h is not None:
            return h
        now = self.clock()
        rows = self.ot.q(f"SELECT ts, state, {', '.join(SIGNAL_COLS)} FROM historian WHERE asset_id=? AND state='run' "
                         "AND ts >= ? ORDER BY ts", (asset_id, iso(now - timedelta(minutes=max(self.window_min, 30)))))
        if not rows:
            rows = self.ot.q(f"SELECT ts, state, {', '.join(SIGNAL_COLS)} FROM historian WHERE asset_id=? AND state='run' "
                             "ORDER BY ts DESC LIMIT 6", (asset_id,))
        if not rows:
            raise KeyError(asset_id)
        msgs = [TelemetryMessage(asset_id=asset_id, line_id="L1", ts=parse_ts(r["ts"]), state=MachineState.RUN,
                                 values={c: r[c] for c in SIGNAL_COLS}) for r in rows]
        msgs.sort(key=lambda m: m.ts)
        return self.model.score(self.extractor.extract(asset_id, msgs, self.hours(asset_id)))

    def health_since(self, asset_id: str, since: datetime, min_rows: int = 3) -> AssetHealth | None:
        """A score that uses only readings taken after `since` (e.g. the restart after a repair), or None if there are
        not enough yet. The latest live score is used when it was computed after `since` (its window restarts with the
        machine); otherwise the historian's running samples since then are scored. Never returns a pre-repair score."""
        h = self.scores.get(asset_id)
        if h is not None and h.evaluated_at >= since:
            return h
        rows = self.ot.q(f"SELECT ts, state, {', '.join(SIGNAL_COLS)} FROM historian WHERE asset_id=? AND state='run' "
                         "AND ts > ? ORDER BY ts", (asset_id, iso(since)))
        if len(rows) < min_rows:
            return None
        msgs = [TelemetryMessage(asset_id=asset_id, line_id="L1", ts=parse_ts(r["ts"]), state=MachineState.RUN,
                                 values={c: r[c] for c in SIGNAL_COLS}) for r in rows]
        return self.model.score(self.extractor.extract(asset_id, msgs, self.hours(asset_id)))

    def predict(self, window: FeatureWindow) -> AssetHealth:
        return self.model.score(window)

    def models(self) -> list[ModelInfo]:
        return self.model.info()
