import asyncio
from datetime import timedelta

from app.connectors.health import LatestScores
from app.services.scoring import HealthScorer
from data import NOW
from plantguard_contracts.schemas.common import DataQuality, Health, MachineState
from plantguard_contracts.schemas.ml import FEATURES, AssetHealth, FeatureWindow
from plantguard_contracts.schemas.ot import Signal, TelemetryMessage


def msg(asset="EQ-3012", ts=NOW, quality=DataQuality.GOOD):
    """Helper: build a TelemetryMessage."""
    return TelemetryMessage(asset_id=asset, line_id="L3", ts=ts, state=MachineState.RUN, quality=quality,
                            values={Signal.VIBRATION_RMS: 2.1})


class FakeExtractor:
    """Feature extractor stub returning a fixed window."""

    def extract(self, asset_id, buffer, hours_since_service):
        """Return a minimal FeatureWindow for the asset."""
        return FeatureWindow(asset_id=asset_id, window_end=buffer[-1].ts, features={k: 0.0 for k in FEATURES})


class FakeModel:
    """Model stub that always returns the given risk."""

    version = "fake"
    def __init__(self, risk): self.risk = risk
    def score(self, window):
        """Return an AssetHealth with the configured risk."""
        return AssetHealth(asset_id=window.asset_id, health=Health.CRITICAL, risk_72h=self.risk, rul_hours=70,
                           anomaly_score=0.9, predicted_failure_mode="bearing_wear", rule_threshold_breached=False,
                           top_features=[], model_version="fake", evaluated_at=NOW)
    def info(self): return []


class RecordingBus:
    """Bus stub that records what was published."""

    def __init__(self): self.sent = []
    async def publish(self, channel, message): self.sent.append((channel, message))
    def subscribe(self, channel): raise NotImplementedError


def make_scorer(container, risk, clock):
    """Helper: a HealthScorer wired to fakes and a controllable clock."""
    bus = RecordingBus()
    scorer = HealthScorer(FakeExtractor(), FakeModel(risk), bus, LatestScores(), container.directory,
                          lambda a: 100.0, clock, min_samples=3)
    for i in range(5):
        scorer.ingest(msg(ts=NOW + timedelta(seconds=i)))
    return scorer, bus


def test_scorer_publishes_health_and_debounces_alerts(container):
    """Health is published every cycle; a high-risk alert only once per 30 min."""
    t = [NOW]
    scorer, bus = make_scorer(container, 0.78, lambda: t[0])
    asyncio.run(scorer.evaluate())
    asyncio.run(scorer.evaluate())
    channels = [c for c, _ in bus.sent]
    assert channels.count("ml.health.EQ-3012") == 2 and channels.count("alerts.raised") == 1
    t[0] += timedelta(minutes=31)
    asyncio.run(scorer.evaluate())
    assert [c for c, _ in bus.sent].count("alerts.raised") == 2
    alert = next(m for c, m in bus.sent if c == "alerts.raised")
    assert alert.plant_id == "PLANT-A" and alert.line_id == "L3" and alert.severity.value == "high"


def test_scorer_ignores_low_risk_and_bad_quality(container):
    """Low risk raises no alert and non-GOOD readings are not scored."""
    scorer, bus = make_scorer(container, 0.2, lambda: NOW)
    asyncio.run(scorer.evaluate())
    assert "alerts.raised" not in [c for c, _ in bus.sent]
    scorer.ingest(msg("EQ-3011", quality=DataQuality.STALE))
    assert "EQ-3011" not in scorer._buffers
