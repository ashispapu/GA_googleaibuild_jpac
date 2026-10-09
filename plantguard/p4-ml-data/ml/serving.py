"""load_model (ML-3). Backend calls load_model("p4-ml-data/models/latest") once at startup.
Artifacts: failure_clf.joblib, anomaly.joblib, feature_list.json, metrics.json, shap_background.npy
score() < 50 ms, deterministic. Story window must give risk_72h >= 0.7 with rule_threshold_breached = False."""
from plantguard_contracts.schemas.ml import AssetHealth, FeatureWindow, ModelInfo


class PlantModel:  # implements interfaces.PredictiveModel
    version = "fp-xgb-1.0.0"

    def __init__(self, path: str):
        self.path = path  # TODO: joblib.load(...)

    def score(self, window: FeatureWindow) -> AssetHealth:
        raise NotImplementedError

    def info(self) -> list[ModelInfo]:
        raise NotImplementedError


def load_model(path: str) -> PlantModel:
    return PlantModel(path)
