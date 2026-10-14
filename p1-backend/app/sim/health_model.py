"""Health model for the simulated plant (implements the C5 FeatureExtractor + PredictiveModel contracts).

Physics-informed signature matching: a 10-minute window of readings is compared with the machine's healthy
reference at its current speed. For each known failure signature (bearing wear, misalignment, overheating) the model
fits how far that fault would have to be developed to explain the deviation, keeps the best fit, estimates the wear
rate from the 6-hour trend and turns the remaining life into a 72-hour failure risk. Contributions per feature are
exact for the fitted linear deviation (like SHAP for a linear model).

It only sees sensor data (never the simulator's ground truth), so it can be fooled the same way a real model can:
a loose misalignment is impulsive and looks like bearing wear.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable

from plantguard_contracts.schemas.common import Health
from plantguard_contracts.schemas.ml import FEATURES, AssetHealth, FeatureWindow, ModelInfo, ShapFeature
from plantguard_contracts.schemas.ot import TelemetryMessage

from app.sim.physics import nominal

VERSION = "pg-sig-1.0.0"
KNOWN = ("bearing_wear", "misalignment", "overheating")  # the model was trained on these signatures only
# typical noise of a 10-min mean, per deviation (sets how much each one counts in the fit)
SCALE = {"vib": 0.04, "kurt": 0.12, "bt": 0.8, "cur": 0.015, "pw": 0.015}

PastMeans = Callable[[str, datetime, float], dict[str, float] | None]  # (asset, now, hours ago) -> means


class SignatureFeatureExtractor:  # implements interfaces.FeatureExtractor
    def __init__(self, baselines: dict[str, dict], past: PastMeans):
        self._base, self._past = baselines, past

    def extract(self, asset_id: str, buffer: list[TelemetryMessage], hours_since_service: float) -> FeatureWindow:
        vals = lambda s: [float(m.values[s]) for m in buffer if s in m.values]  # noqa: E731
        mean = lambda s: (sum(v) / len(v)) if (v := vals(s)) else 0.0  # noqa: E731
        end = buffer[-1].ts
        b = self._base[asset_id]
        rpm = mean("spindle_rpm")
        speed = min(100.0, max(1.0, 100 * rpm / b["spindle_rpm"])) if b["spindle_rpm"] else 100.0
        ref = nominal(b, speed)
        past = self._past(asset_id, end, 6.0) or {}
        vib, bt = mean("vibration_rms"), mean("bearing_temp")
        feats = {
            "vib_rms_mean": vib,
            "vib_rms_trend_6h": (vib / past["vibration_rms"] - 1) if past.get("vibration_rms") else 0.0,
            "vib_peak_max": max(vals("vibration_peak") or [0.0]),
            "vib_kurtosis_mean": mean("vibration_kurtosis"),
            "bearing_temp_mean": bt,
            "bearing_temp_trend_6h": (bt - past["bearing_temp"]) if past.get("bearing_temp") else 0.0,
            "motor_current_mean": mean("motor_current"),
            "power_kw_vs_baseline": (mean("power_kw") / ref["power_kw"] - 1) if ref["power_kw"] else 0.0,
            "spindle_rpm_mean": rpm,
            "hours_since_service": float(hours_since_service),
        }
        assert set(feats) == set(FEATURES)
        return FeatureWindow(asset_id=asset_id, window_end=end, features={k: round(v, 4) for k, v in feats.items()})


class SignatureHealthModel:  # implements interfaces.PredictiveModel
    version = VERSION

    def __init__(self, baselines: dict[str, dict], signatures: dict[str, dict], vib_rule: dict[str, float],
                 trained_at: datetime, metrics: dict[str, float] | None = None):
        self._base, self._sig, self._rule = baselines, signatures, vib_rule
        self._trained_at, self.metrics = trained_at, metrics or {}

    # ---- fitting ----
    def _deviation(self, asset_id: str, f: dict[str, float]) -> tuple[dict[str, float], float]:
        b = self._base[asset_id]
        speed = min(100.0, max(1.0, 100 * f["spindle_rpm_mean"] / b["spindle_rpm"])) if b["spindle_rpm"] else 100.0
        ref = nominal(b, speed)
        d = {"vib": f["vib_rms_mean"] / ref["vibration_rms"] - 1 if ref["vibration_rms"] else 0.0,
             "kurt": f["vib_kurtosis_mean"] - 3.0,
             "bt": f["bearing_temp_mean"] - ref["bearing_temp"],
             "cur": f["motor_current_mean"] / ref["motor_current"] - 1 if ref["motor_current"] else 0.0,
             "pw": f["power_kw_vs_baseline"]}
        return d, speed / 100

    def _expected(self, mode: str, sf: float) -> dict[str, float]:
        s = self._sig[mode]
        return {"vib": (s["vibration_rms_x"] - 1) * (0.6 + 0.4 * sf), "kurt": s["kurtosis_add"],
                "bt": s["bearing_temp_add"], "cur": s["current_x"] - 1, "pw": s["power_x"] - 1}

    def fit(self, asset_id: str, f: dict[str, float]) -> tuple[str, float, float, dict[str, float]]:
        """(mode, progress, residual, per-signal contribution) of the best-fitting known signature."""
        d, sf = self._deviation(asset_id, f)
        best = None
        for mode in KNOWN:
            e = self._expected(mode, sf)
            w = {k: 1 / SCALE[k] ** 2 for k in d}
            num = sum(w[k] * e[k] * d[k] for k in d)
            den = sum(w[k] * e[k] ** 2 for k in d) or 1e-9
            p = max(0.0, min(1.3, num / den))
            res = sum(w[k] * (d[k] - p * e[k]) ** 2 for k in d)
            if best is None or res < best[2]:
                contrib = {k: w[k] * e[k] * d[k] / den for k in d}
                best = (mode, p, res, contrib)
        return best

    def _progress_6h_ago(self, asset_id: str, mode: str, f: dict[str, float]) -> float | None:
        """Progress 6 h ago from the trends (vibration and bearing temperature are the ones we keep a trend of)."""
        b = self._base[asset_id]
        speed = min(100.0, max(1.0, 100 * f["spindle_rpm_mean"] / b["spindle_rpm"])) if b["spindle_rpm"] else 100.0
        ref = nominal(b, speed)
        e = self._expected(mode, speed / 100)
        tr = f["vib_rms_trend_6h"]
        if tr == 0.0 and f["bearing_temp_trend_6h"] == 0.0:
            return None
        vib_then = f["vib_rms_mean"] / (1 + tr) if tr > -0.99 else f["vib_rms_mean"]
        d_vib = vib_then / ref["vibration_rms"] - 1
        d_bt = (f["bearing_temp_mean"] - f["bearing_temp_trend_6h"]) - ref["bearing_temp"]
        w_v, w_b = 1 / SCALE["vib"] ** 2, 1 / SCALE["bt"] ** 2
        den = w_v * e["vib"] ** 2 + w_b * e["bt"] ** 2
        return max(0.0, (w_v * e["vib"] * d_vib + w_b * e["bt"] * d_bt) / den) if den else None

    # ---- PredictiveModel ----
    def score(self, window: FeatureWindow) -> AssetHealth:
        f, aid = window.features, window.asset_id
        mode, p, _res, contrib = self.fit(aid, f)
        p_then = self._progress_6h_ago(aid, mode, f)
        rate = (p - p_then) / 6.0 if p_then is not None else 0.0
        rate = max(rate, 1 / 200)  # never assume a fault stands still
        rul = max(0.5, (1.0 - min(p, 1.0)) / rate) if p < 1.0 else 0.5
        gate = _smoothstep(p, 0.12, 0.30)
        risk = round(min(0.99, gate * (1 - math.exp(-72 / (1.2 * rul)))), 2)
        health = Health.CRITICAL if risk >= 0.7 else Health.WARNING if risk >= 0.3 else Health.OK
        rule = f["vib_rms_mean"] >= self._rule.get(aid, 1e9)
        top = self._explain(mode, p, contrib, f)
        return AssetHealth(asset_id=aid, health=health, risk_72h=risk, rul_hours=round(rul if gate > 0.05 else 2000, 1),
                           anomaly_score=round(min(1.0, p * 1.15), 2),
                           predicted_failure_mode=mode if p >= 0.15 else None, rule_threshold_breached=rule,
                           top_features=top, model_version=VERSION, evaluated_at=window.window_end)

    def _explain(self, mode: str, p: float, contrib: dict[str, float], f: dict[str, float]) -> list[ShapFeature]:
        total = sum(abs(v) for v in contrib.values()) or 1.0
        share = {k: p * v / total for k, v in contrib.items()}
        items = [
            ShapFeature(name="vib_rms_trend_6h", value=round(f["vib_rms_trend_6h"], 3), contribution=round(share["vib"], 3),
                        description=f"Vibration RMS {f['vib_rms_trend_6h']:+.0%} in 6 h"),
            ShapFeature(name="vib_kurtosis_mean", value=round(f["vib_kurtosis_mean"], 2), contribution=round(share["kurt"], 3),
                        description=f"Kurtosis {f['vib_kurtosis_mean']:.1f}" + (" (impulsive, bearing wear)"
                                                                                if f["vib_kurtosis_mean"] > 4 else "")),
            ShapFeature(name="bearing_temp_trend_6h", value=round(f["bearing_temp_trend_6h"], 2),
                        contribution=round(share["bt"], 3),
                        description=f"Bearing temp {f['bearing_temp_trend_6h']:+.1f} °C in 6 h"),
            ShapFeature(name="power_kw_vs_baseline", value=round(f["power_kw_vs_baseline"], 3), contribution=round(share["pw"], 3),
                        description=f"Power draw {f['power_kw_vs_baseline']:+.0%} vs baseline"),
            ShapFeature(name="motor_current_mean", value=round(f["motor_current_mean"], 2), contribution=round(share["cur"], 3),
                        description=f"Motor current {f['motor_current_mean']:.1f} A"),
            ShapFeature(name="hours_since_service", value=round(f["hours_since_service"], 0), contribution=0.0,
                        description=f"{f['hours_since_service']:.0f} h since last service"),
        ]
        items.sort(key=lambda s: -abs(s.contribution))
        return items[:5]

    def info(self) -> list[ModelInfo]:
        return [ModelInfo(version=VERSION, kind="failure_classifier", algorithm="physics-informed signature matching",
                          metrics=self.metrics, feature_names=list(FEATURES), trained_at=self._trained_at)]


def _smoothstep(x: float, a: float, b: float) -> float:
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def backtest(model: SignatureHealthModel, extractor_baselines: dict[str, dict], signatures: dict[str, dict],
             rule: dict[str, float], seed: int = 7, n: int = 120) -> dict[str, float]:
    """Quick hold-out check on simulated windows (computed at start-up, shown on the Accuracy page):
    precision/recall of 'alert within 72 h of breakdown', failure-mode accuracy, and lead time over the vibration rule."""
    import random

    from app.sim.physics import Condition, readings
    rng = random.Random(seed)
    assets = list(extractor_baselines)
    tp = fp = fn = tn = mode_ok = mode_n = 0
    lead = []
    variants = ["bearing_wear", "misalignment", "misalignment_loose", "overheating"]
    for _ in range(n):
        aid = rng.choice(assets)
        b = extractor_baselines[aid]
        healthy = rng.random() < 0.4
        variant = rng.choice(variants)
        mode = variant.split("_loose")[0]
        T = rng.uniform(30, 90)
        first_alert = first_rule = None
        for h in range(0, int(T) + 1, 2):
            p = 0.0 if healthy else h / T
            p6 = 0.0 if healthy else max(0.0, (h - 6) / T)
            cond = None if healthy else Condition(mode, variant, p)
            now = readings(b, signatures, cond, 100, "run", rng, 0.01)
            then = readings(b, signatures, None if healthy else Condition(mode, variant, p6), 100, "run", rng, 0.01)
            f = {"vib_rms_mean": now["vibration_rms"], "vib_rms_trend_6h": now["vibration_rms"] / then["vibration_rms"] - 1,
                 "vib_peak_max": now["vibration_peak"], "vib_kurtosis_mean": now["vibration_kurtosis"],
                 "bearing_temp_mean": now["bearing_temp"], "bearing_temp_trend_6h": now["bearing_temp"] - then["bearing_temp"],
                 "motor_current_mean": now["motor_current"],
                 "power_kw_vs_baseline": now["power_kw"] / nominal(b, 100)["power_kw"] - 1,
                 "spindle_rpm_mean": now["spindle_rpm"], "hours_since_service": 1000.0}
            hl = model.score(FeatureWindow(asset_id=aid, window_end=datetime(2026, 1, 1), features=f))
            if hl.risk_72h >= 0.7 and first_alert is None:
                first_alert = h
                if not healthy:
                    mode_n += 1
                    mode_ok += int(hl.predicted_failure_mode == mode)
            if now["vibration_rms"] >= rule[aid] and first_rule is None:
                first_rule = h
            if healthy and h >= 24:
                break
        if healthy:
            fp += first_alert is not None
            tn += first_alert is None
        else:
            if first_alert is not None and T - first_alert <= 72:
                tp += 1
                lead.append((first_rule if first_rule is not None else T) - first_alert)
            else:
                fn += 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": round(prec, 2), "recall": round(rec, 2),
            "f1": round(2 * prec * rec / (prec + rec), 2) if prec + rec else 0.0,
            "mode_accuracy": round(mode_ok / mode_n, 2) if mode_n else 0.0,
            "lead_time_gain_hours": round(sum(lead) / len(lead), 1) if lead else 0.0}
