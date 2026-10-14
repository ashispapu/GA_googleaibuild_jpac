"""Contract between the ML package (owner: ML & Data) and the backend / agents."""
from datetime import datetime
from pydantic import Field
from .common import AssetId, Contract, Health, Probability

# Feature names the model is trained on. Feature extraction MUST produce exactly these keys.
FEATURES: dict[str, str] = {
    "vib_rms_mean": "Mean vibration RMS over window (mm/s)",
    "vib_rms_trend_6h": "Relative change of vibration RMS vs 6 h ago (ratio, 0.34 = +34%)",
    "vib_peak_max": "Max vibration peak in window (mm/s)",
    "vib_kurtosis_mean": "Mean kurtosis (impulsiveness, bearing wear indicator)",
    "bearing_temp_mean": "Mean bearing temperature (°C)",
    "bearing_temp_trend_6h": "Change of bearing temperature vs 6 h ago (°C)",
    "motor_current_mean": "Mean motor current (A)",
    "power_kw_vs_baseline": "Power draw vs asset baseline (ratio, 0.08 = +8%)",
    "spindle_rpm_mean": "Mean spindle speed (rpm)",
    "hours_since_service": "Operating hours since last maintenance (h)",
}
WINDOW_MINUTES = 10


class FeatureWindow(Contract):
    asset_id: AssetId
    window_end: datetime
    window_minutes: int = WINDOW_MINUTES
    features: dict[str, float]


class ShapFeature(Contract):
    name: str  # a key of FEATURES
    value: float
    contribution: float  # signed SHAP value toward failure
    description: str  # human text for the UI, e.g. "Vibration RMS +34% in 6 h"


class AssetHealth(Contract):
    asset_id: AssetId
    health: Health
    risk_72h: Probability  # failure probability within 72 h
    rul_hours: float = Field(ge=0)  # remaining useful life estimate
    anomaly_score: Probability
    predicted_failure_mode: str | None = None  # bearing_wear | overheating | misalignment
    rule_threshold_breached: bool  # classic threshold rule; False while ML already warns
    top_features: list[ShapFeature]
    model_version: str
    evaluated_at: datetime


class PredictRequest(Contract):
    window: FeatureWindow


class ModelInfo(Contract):
    version: str
    kind: str  # anomaly | failure_classifier
    algorithm: str  # isolation_forest | xgboost | lightgbm
    metrics: dict[str, float]  # precision, recall, f1, roc_auc, lead_time_gain_hours
    feature_names: list[str]
    trained_at: datetime
