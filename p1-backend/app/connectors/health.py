"""HealthProvider implementations. Fixture first; a live model slots in via LiveHealthProvider (C5)."""
import threading

from plantguard_contracts.interfaces import PredictiveModel
from plantguard_contracts.schemas.common import Health
from plantguard_contracts.schemas.ml import AssetHealth, FeatureWindow, ModelInfo
from pydantic import TypeAdapter

from app.connectors.store import FixtureStore
from app.core.ports import HealthProvider, SceneView


class FixtureHealthProvider:  # implements HealthProvider
    """Health from health_{asset}.json; calm scenes return a healthy reading."""

    def __init__(self, store: FixtureStore, scene: SceneView):
        """store: fixture files; scene: tells whether the scene is calm."""
        self._store, self._scene = store, scene

    def get_health(self, asset_id: str) -> AssetHealth:
        """AssetHealth for an asset; calm scenes override it with low-risk values."""
        h = AssetHealth(**self._store.load(f"health_{asset_id}"))
        if self._scene.is_calm():
            h = h.model_copy(update=dict(health=Health.OK, risk_72h=0.04, anomaly_score=0.1, rul_hours=2000,
                                         predicted_failure_mode=None, rule_threshold_breached=False))
        return h

    def predict(self, window: FeatureWindow) -> AssetHealth:
        """Fixture behaviour: ignore the window and return the asset's health."""
        return self.get_health(window.asset_id)

    def models(self) -> list[ModelInfo]:
        """Model info from models.json."""
        return TypeAdapter(list[ModelInfo]).validate_python(self._store.load("models"))


class LatestScores:
    """Thread-safe cache of the newest AssetHealth per asset, written by the scoring loop."""

    def __init__(self):
        """Start with no scores."""
        self._by_asset: dict[str, AssetHealth] = {}
        self._lock = threading.Lock()

    def put(self, health: AssetHealth) -> None:
        """Store the newest score of an asset."""
        with self._lock:
            self._by_asset[health.asset_id] = health

    def get(self, asset_id: str) -> AssetHealth | None:
        """Newest score of an asset, or None."""
        with self._lock:
            return self._by_asset.get(asset_id)

    def invalidate(self, asset_ids: list[str]) -> None:
        """Drop only local cached copies; the engine owns the authoritative shared scores."""
        with self._lock:
            for aid in asset_ids:
                self._by_asset.pop(aid, None)


class SharedScores(LatestScores):
    """LatestScores in Redis: the engine's scorer writes, every API / worker instance reads (1 s local cache)."""

    def __init__(self, kv, ttl_s: float = 1.0):
        super().__init__()
        import time as _t
        self._kv, self._ttl, self._t = kv, ttl_s, _t
        self._seen: dict[str, float] = {}

    def put(self, health: AssetHealth) -> None:
        super().put(health)
        self._seen[health.asset_id] = self._t.monotonic()
        self._kv.put_json("scores", health.asset_id, health.model_dump(mode="json"))

    def get(self, asset_id: str) -> AssetHealth | None:
        now = self._t.monotonic()
        if now - self._seen.get(asset_id, -1e9) < self._ttl:
            return super().get(asset_id)
        raw = self._kv.get_json("scores", asset_id)
        self._seen[asset_id] = now
        if raw is None:
            return super().get(asset_id)
        h = AssetHealth.model_validate(raw)
        LatestScores.put(self, h)
        return h

    def invalidate(self, asset_ids: list[str]) -> None:
        super().invalidate(asset_ids)
        for aid in asset_ids:
            self._seen.pop(aid, None)


class LiveHealthProvider:  # implements HealthProvider
    """Real model output when available, otherwise the fallback (so the demo never goes blank)."""

    def __init__(self, model: PredictiveModel, scores: LatestScores, fallback: HealthProvider):
        """model: P4 model; scores: latest live scores; fallback: provider used when none yet."""
        self._model, self._scores, self._fallback = model, scores, fallback

    def get_health(self, asset_id: str) -> AssetHealth:
        """Newest live score, else the fallback's."""
        return self._scores.get(asset_id) or self._fallback.get_health(asset_id)

    def predict(self, window: FeatureWindow) -> AssetHealth:
        """Score the given window with the real model."""
        return self._model.score(window)

    def models(self) -> list[ModelInfo]:
        """Info of the loaded model."""
        return self._model.info()
