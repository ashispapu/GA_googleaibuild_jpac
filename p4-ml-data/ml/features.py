"""FeatureExtractor (ML-2). Output keys MUST equal plantguard_contracts.schemas.ml.FEATURES."""
from plantguard_contracts.schemas.ml import FEATURES, FeatureWindow
from plantguard_contracts.schemas.ot import TelemetryMessage


class WindowFeatureExtractor:  # implements interfaces.FeatureExtractor
    def extract(self, asset_id: str, buffer: list[TelemetryMessage], hours_since_service: float) -> FeatureWindow:
        feats: dict[str, float] = {}
        # TODO: compute every key in FEATURES from buffer
        assert set(feats) == set(FEATURES), "feature keys must match the contract"
        return FeatureWindow(asset_id=asset_id, window_end=buffer[-1].ts, features=feats)
