"""Scoring loop (BE-5): buffer 10 min of telemetry per asset -> P4 features -> P4 model -> bus.
Publishes ml.health.{asset} each cycle and alerts.raised when risk_72h >= threshold (debounced per asset)."""
import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Callable

from plantguard_contracts.interfaces import EventBus, FeatureExtractor, PredictiveModel
from plantguard_contracts.schemas.bus import AlertRaised
from plantguard_contracts.schemas.common import DataQuality
from plantguard_contracts.schemas.incidents import Severity
from plantguard_contracts.schemas.ml import WINDOW_MINUTES, AssetHealth
from plantguard_contracts.schemas.ot import TelemetryMessage

from app.core.common import Clock, utcnow
from app.services.plant_directory import PlantDirectory
from app.connectors.health import LatestScores
from app.services.ot_gateway import OTGateway, StalenessWatchdog

log = logging.getLogger("plantguard.scoring")
ALERT_RISK = 0.7
DEBOUNCE = timedelta(minutes=30)
MIN_SAMPLES = 10


def severity_for(risk: float) -> Severity:
    """Map a 72 h risk to a severity: >= 0.7 high, >= 0.3 medium, else low."""
    return Severity.HIGH if risk >= ALERT_RISK else Severity.MEDIUM if risk >= 0.3 else Severity.LOW


class HealthScorer:
    """Buffers recent good telemetry per asset, scores it with the ML model and raises debounced alerts."""

    def __init__(self, extractor: FeatureExtractor, model: PredictiveModel, bus: EventBus, scores: LatestScores,
                 directory: PlantDirectory, hours_since_service: Callable[[str], float], clock: Clock = utcnow,
                 min_samples: int = MIN_SAMPLES):
        """extractor/model: P4's features and model; bus/scores: outputs; directory: asset lookup; hours_since_service:
        callback; min_samples: readings needed before scoring."""
        self._extractor, self._model, self._bus, self._scores = extractor, model, bus, scores
        self._dir, self._hours, self._clock, self._min = directory, hours_since_service, clock, min_samples
        self._buffers: dict[str, deque[TelemetryMessage]] = defaultdict(deque)
        self._last_alert: dict[str, datetime] = {}

    def ingest(self, msg: TelemetryMessage) -> None:
        """Add a message to its asset's buffer (GOOD quality only) and drop data older than the window."""
        if msg.quality != DataQuality.GOOD:
            return  # never score on stale or out-of-range data
        buf = self._buffers[msg.asset_id]
        buf.append(msg)
        cutoff = msg.ts - timedelta(minutes=WINDOW_MINUTES)
        while buf and buf[0].ts < cutoff:
            buf.popleft()

    async def evaluate(self) -> list[AssetHealth]:
        """Score every asset with enough samples, cache the result, publish ml.health.{asset} and maybe an alert; returns
        the scores."""
        out = []
        for asset_id, buf in list(self._buffers.items()):
            if len(buf) < self._min:
                continue
            window = self._extractor.extract(asset_id, list(buf), self._hours(asset_id))
            health = self._model.score(window)
            self._scores.put(health)
            await self._bus.publish(f"ml.health.{asset_id}", health)
            await self._maybe_alert(health)
            out.append(health)
        return out

    async def _maybe_alert(self, health: AssetHealth) -> None:
        """Publish alerts.raised if risk >= 0.7 and no alert for this asset in the last 30 min."""
        if health.risk_72h < ALERT_RISK:
            return
        now = self._clock()
        last = self._last_alert.get(health.asset_id)
        if last and now - last < DEBOUNCE:
            return
        self._last_alert[health.asset_id] = now
        loc = self._dir.locate(health.asset_id)
        reason = f"ML risk {health.risk_72h:.0%} in 72 h" + (
            "; rule threshold not breached yet" if not health.rule_threshold_breached else "")
        await self._bus.publish("alerts.raised", AlertRaised(
            asset_id=health.asset_id, plant_id=loc.plant_id, line_id=loc.line_id,
            severity=severity_for(health.risk_72h), risk_72h=health.risk_72h, reason=reason, ts=now))


class ScoringService:
    """Background wiring: ingest every line, publish telemetry, score each interval, flag stale assets."""

    def __init__(self, gateway: OTGateway, scorer: HealthScorer, bus: EventBus, directory: PlantDirectory,
                 lines: list[tuple[str, str]], interval_s: float = 60.0, clock: Clock = utcnow):
        """gateway: telemetry in; scorer/bus/directory: processing; lines: (plant, line) pairs; interval_s: seconds
        between scoring cycles."""
        self._gw, self._scorer, self._bus, self._dir = gateway, scorer, bus, directory
        self._lines, self._interval, self._clock = lines, interval_s, clock
        self._watchdog = StalenessWatchdog()

    async def run(self) -> None:
        """Start one ingest task per line plus the scoring and staleness loops; cancel all on exit."""
        tasks = [asyncio.create_task(self._ingest(p, l)) for p, l in self._lines]
        tasks += [asyncio.create_task(self._score_loop()), asyncio.create_task(self._stale_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def _ingest(self, plant_id: str, line_id: str) -> None:
        """For each message of a line: watch staleness, feed the scorer, publish ot.telemetry.*."""
        async for msg in self._gw.stream(plant_id, line_id):
            self._watchdog.observe(msg)
            self._scorer.ingest(msg)
            await self._bus.publish(f"ot.telemetry.{plant_id}.{line_id}", msg)

    async def _score_loop(self) -> None:
        """Every interval run a scoring cycle; log and continue if one fails."""
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._scorer.evaluate()
            except Exception:  # one bad cycle must not stop monitoring
                log.exception("scoring cycle failed")

    async def _stale_loop(self) -> None:
        """Every second publish STALE copies for assets that stopped sending."""
        while True:
            await asyncio.sleep(1)
            for msg in self._watchdog.stale(self._clock()):
                self._watchdog.observe(msg)
                await self._bus.publish(f"ot.telemetry.{self._dir.locate(msg.asset_id).plant_id}.{msg.line_id}", msg)
