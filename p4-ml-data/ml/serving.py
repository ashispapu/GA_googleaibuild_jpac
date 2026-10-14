"""load_model (ML-3). The backend calls load_model("p4-ml-data/models/latest") once at startup, then
model.score(window) for each FeatureWindow from ml.features.WindowFeatureExtractor.

Artifacts in models/<version>/ (C5): failure_clf.joblib, anomaly.joblib, feature_list.json, metrics.json,
shap_background.npy - plus calibrator.joblib, mode_clf.joblib, rul_reg.joblib, asset_baselines.json, model_meta.json.

score() is deterministic (no state, no randomness) and takes a few milliseconds.

The .joblib files hold only plain Python values (XGBoost models as XGBoost's portable model bytes, the Isolation
Forest and the calibration as plain arrays), so they don't depend on pickle versions. XGBoost must be at least the
version the model was trained with (older ones read the file but predict wrongly), which load_model checks.
manifest.json lists size + SHA-256 of each file; a damaged or half-copied file gives a clear error.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import xgboost as xgb

from plantguard_contracts.schemas.common import Health
from plantguard_contracts.schemas.ml import FEATURES, AssetHealth, FeatureWindow, ModelInfo, ShapFeature

from .features import FEATURE_NAMES, baseline_for, resolve_model_dir

RULE_RMS = 4.5                 # C5 classic rule (here judged on the 10-minute mean; the full rule needs 30 min)
HEALTHY_RUL_H = 2000.0         # "no breakdown expected soon"
TOP_N = 5


def _band(risk: float) -> Health:
    return Health.CRITICAL if risk >= 0.7 else Health.WARNING if risk >= 0.3 else Health.OK


class PlantModel:  # implements interfaces.PredictiveModel
    version = "fp-xgb-1.0.0"

    def __init__(self, path: str | Path):
        d = resolve_model_dir(path)
        self.path = d
        names = json.loads((d / "feature_list.json").read_text(encoding="utf-8"))
        if names != FEATURE_NAMES:
            raise ValueError(f"{d}: feature_list.json does not match schemas.ml.FEATURES - retrain the model")
        self.meta = json.loads((d / "model_meta.json").read_text(encoding="utf-8")) if (d / "model_meta.json").exists() else {}
        self.version = self.meta.get("version", self.version)
        self.metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        self.baselines = json.loads((d / "asset_baselines.json").read_text(encoding="utf-8"))
        _check_files(d)
        self._clf = _booster(joblib.load(d / "failure_clf.joblib"), "failure_clf.joblib")
        cal = joblib.load(d / "calibrator.joblib") if (d / "calibrator.joblib").exists() else None
        self._cal = (np.asarray(cal["x"], dtype=float), np.asarray(cal["y"], dtype=float)) if cal else None
        an = joblib.load(d / "anomaly.joblib")
        self._iso = _IsolationForest(an)
        self._q99, self._an_scale = an["q99"], an["scale"]
        mode = joblib.load(d / "mode_clf.joblib")
        self._mode, self._modes = _booster(mode, "mode_clf.joblib"), list(mode["classes"])
        self._rul = _booster(joblib.load(d / "rul_reg.joblib"), "rul_reg.joblib")
        bg = np.load(d / "shap_background.npy")                 # healthy reference windows
        self._bg_raw = float(np.mean(self._clf.inplace_predict(bg)))

    # ------------------------------------------------------------------ scoring
    def _x(self, window: FeatureWindow) -> np.ndarray:
        missing = set(FEATURES) - set(window.features)
        extra = set(window.features) - set(FEATURES)
        if missing or extra:
            raise ValueError(f"feature keys must equal schemas.ml.FEATURES (missing {sorted(missing)}, "
                             f"unexpected {sorted(extra)})")
        return np.array([[window.features[f] for f in FEATURE_NAMES]], dtype=np.float32)

    def _risk_raw(self, x: np.ndarray) -> float:
        return float(self._clf.inplace_predict(x)[0])

    def score(self, window: FeatureWindow) -> AssetHealth:
        x = self._x(window)
        raw = self._risk_raw(x)
        risk = float(np.interp(raw, *self._cal)) if self._cal is not None else raw
        risk = round(min(max(risk, 0.0), 1.0), 3)

        # SHAP (TreeSHAP built into XGBoost), rescaled from log-odds to probability points so the values read as
        # "how much this pushed the risk up", relative to a healthy machine
        c = self._clf.predict(xgb.DMatrix(x), pred_contribs=True)[0][:-1]
        s = float(c.sum())
        scale = (raw - self._bg_raw) / s if abs(s) > 1e-9 else 0.0
        contrib = c * scale
        order = np.argsort(-contrib)[:TOP_N]
        base = baseline_for(self.baselines, window.asset_id)
        top = [ShapFeature(name=FEATURE_NAMES[i], value=round(float(x[0, i]), 4), contribution=round(float(contrib[i]), 4),
                           description=describe(FEATURE_NAMES[i], float(x[0, i]), base)) for i in order]

        raw_an = self._iso.anomaly(x[0])
        anomaly = round(float(1 / (1 + np.exp(-(raw_an - self._q99) / self._an_scale))), 3)
        mode, rul = None, HEALTHY_RUL_H
        if risk >= 0.3:
            mode = self._modes[int(np.argmax(self._mode.inplace_predict(x)[0]))]
            rul = round(float(np.clip(self._rul.inplace_predict(x)[0], 1.0, 240.0)), 1)
        return AssetHealth(
            asset_id=window.asset_id, health=_band(risk), risk_72h=risk, rul_hours=rul, anomaly_score=anomaly,
            predicted_failure_mode=mode, rule_threshold_breached=bool(window.features["vib_rms_mean"] > RULE_RMS),
            top_features=top, model_version=self.version, evaluated_at=window.window_end)

    # ------------------------------------------------------------------ model page
    def info(self) -> list[ModelInfo]:
        trained = datetime.fromisoformat(self.meta["trained_at"]) if "trained_at" in self.meta \
            else datetime.fromtimestamp((self.path / "failure_clf.joblib").stat().st_mtime, timezone.utc)
        return [
            ModelInfo(version=self.version, kind="failure_classifier", algorithm="xgboost",
                      metrics={k: float(v) for k, v in self.metrics.items()}, feature_names=FEATURE_NAMES,
                      trained_at=trained),
            ModelInfo(version=self.meta.get("anomaly_version", "an-if-1.0.0"), kind="anomaly",
                      algorithm="isolation_forest",
                      metrics={k: float(v) for k, v in self.meta.get("anomaly_metrics", {}).items()},
                      feature_names=FEATURE_NAMES, trained_at=trained),
        ]


def _check_files(d: Path) -> None:
    man = d / "manifest.json"
    if not man.exists():
        return
    for name, want in json.loads(man.read_text(encoding="utf-8")).items():
        f = d / name
        if not f.exists():
            raise FileNotFoundError(f"{f} is missing - copy the whole models/{d.name}/ folder again")
        data = f.read_bytes()
        if len(data) != want["bytes"] or hashlib.sha256(data).hexdigest() != want["sha256"]:
            raise ValueError(f"{f} is damaged ({len(data):,} bytes, expected {want['bytes']:,}). Copy it again from "
                             f"the zip or git. If git changed it, check that .gitattributes marks *.joblib as binary.")


def _booster(blob: dict, name: str) -> xgb.Booster:
    if not isinstance(blob, dict) or blob.get("format") != "xgboost-ubj":
        raise ValueError(f"{name}: old model format - retrain with `python -m ml.train`")
    need, have = _ver(blob.get("xgboost_version", "0")), _ver(xgb.__version__)
    if have < need:
        raise RuntimeError(f"{name} was trained with XGBoost {blob['xgboost_version']}; you have {xgb.__version__}, "
                           f"which reads it wrongly. Run: pip install -U xgboost")
    b = xgb.Booster()
    b.load_model(bytearray(blob["model"]))
    return b


def _ver(v: str) -> tuple[int, int]:
    parts = (v.split(".") + ["0", "0"])[:2]
    return tuple(int("".join(ch for ch in p if ch.isdigit()) or 0) for p in parts)


def _c(n: float) -> float:
    """Average path length of an unsuccessful search in a binary tree with n points (Isolation Forest paper)."""
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1.0) + 0.5772156649015329) - 2.0 * (n - 1.0) / n


class _IsolationForest:
    """Scores one row the same way scikit-learn's IsolationForest.score_samples does (higher = more unusual)."""

    def __init__(self, blob: dict):
        if blob.get("format") != "isolation-forest":
            raise ValueError("anomaly.joblib: old model format - retrain with `python -m ml.train`")
        self.trees = blob["trees"]
        self.norm = _c(blob["max_samples"])

    def anomaly(self, x: np.ndarray) -> float:
        total = 0.0
        for t in self.trees:
            node, depth = 0, 0
            left, right, feat, thr = t["left"], t["right"], t["feature"], t["threshold"]
            while left[node] != -1:
                node = left[node] if x[feat[node]] <= thr[node] else right[node]
                depth += 1
            total += depth + _c(t["n"][node])
        return float(2.0 ** (-(total / len(self.trees)) / self.norm))


def describe(name: str, v: float, base: dict) -> str:
    """Short plain sentence for the UI, e.g. 'Vibration RMS +34% in 6 h'."""
    if name == "vib_rms_mean":
        return f"Vibration {v:.1f} mm/s (normal ~{base['vibration_rms']:.1f})"
    if name == "vib_rms_trend_6h":
        return f"Vibration RMS {v * 100:+.0f}% in 6 h"
    if name == "vib_peak_max":
        return f"Vibration peaks up to {v:.1f} mm/s (normal ~{base['vibration_peak']:.1f})"
    if name == "vib_kurtosis_mean":
        return f"Kurtosis {v:.1f} (impulsive, bearing wear)" if v > 4 else f"Kurtosis {v:.1f} (normal ~3)"
    if name == "bearing_temp_mean":
        return f"Bearing at {v:.0f} °C (normal ~{base['bearing_temp']:.0f} °C)"
    if name == "bearing_temp_trend_6h":
        return f"Bearing temp {v:+.1f} °C in 6 h"
    if name == "motor_current_mean":
        return f"Motor current {v:.1f} A (normal ~{base['motor_current']:.1f} A)"
    if name == "power_kw_vs_baseline":
        return f"Power draw {v * 100:+.0f}% vs baseline"
    if name == "spindle_rpm_mean":
        return f"Speed {v:,.0f} rpm"
    if name == "hours_since_service":
        return f"{v:,.0f} h since last service"
    return f"{FEATURES.get(name, name)}: {v:g}"


def load_model(path: str | Path) -> PlantModel:
    return PlantModel(path)
