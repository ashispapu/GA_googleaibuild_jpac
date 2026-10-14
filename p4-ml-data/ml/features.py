"""FeatureExtractor (ML-2). Output keys MUST equal plantguard_contracts.schemas.ml.FEATURES, in that order.

Two ways in, same numbers out:
  * WindowFeatureExtractor.extract(): live, one 10-minute buffer of TelemetryMessage at a time (backend scorer).
  * window_features(): offline, a whole 1-minute history at once (training). A unit test checks they agree.

The two "vs 6 h ago" features need older data than the 10-minute buffer the backend passes in, so the
extractor remembers each machine's earlier windows itself. Until it has seen 6 hours of a machine, it compares
with that machine's normal level from asset_baselines.json. Keep ONE extractor object alive per process.
"""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from plantguard_contracts.schemas.common import DataQuality, MachineState
from plantguard_contracts.schemas.ml import FEATURES, WINDOW_MINUTES, FeatureWindow
from plantguard_contracts.schemas.ot import Signal, TelemetryMessage

FEATURE_NAMES: list[str] = list(FEATURES)
TREND_HOURS = 6
TREND_TOLERANCE = timedelta(minutes=5)        # an earlier window counts as "6 h ago" if within +-5 min
P4 = Path(__file__).resolve().parents[1]
DEFAULT_BASELINES = P4 / "models" / "latest"
BASE_KEYS = ["vibration_rms", "vibration_peak", "vibration_kurtosis", "bearing_temp", "motor_current", "power_kw",
             "spindle_rpm"]
_SKIP_QUALITY = {DataQuality.OUT_OF_RANGE, DataQuality.MISSING, "out_of_range", "missing"}


# --------------------------------------------------------------------------------------------- baselines
def resolve_model_dir(path: str | Path) -> Path:
    """models/latest may be a folder, a symlink, or a small text file holding the version name (Windows-safe)."""
    p = Path(path)
    if p.is_file():
        p = p.parent / p.read_text(encoding="utf-8").strip()
    return p


def load_baselines(src: str | Path | dict | None = None) -> dict:
    """Normal level of each signal per machine ({"assets": {id: {...}}, "default": {...}})."""
    if isinstance(src, dict):
        return src
    d = resolve_model_dir(src or DEFAULT_BASELINES)
    f = d / "asset_baselines.json" if d.is_dir() else d
    return json.loads(Path(f).read_text(encoding="utf-8"))


def baseline_for(baselines: dict, asset_id: str) -> dict:
    return baselines.get("assets", {}).get(asset_id) or baselines["default"]


# --------------------------------------------------------------------------------------------- live
def _value(values: dict, sig: Signal) -> float | None:
    v = values.get(sig)
    if v is None:
        v = values.get(sig.value)
    return None if v is None else float(v)


class WindowFeatureExtractor:  # implements interfaces.FeatureExtractor
    def __init__(self, baselines: str | Path | dict | None = None):
        self.baselines = load_baselines(baselines)
        self._history: dict[str, deque] = {}       # asset -> (window_end, rms_mean, temp_mean) of running windows

    def reset(self) -> None:
        """Forget all remembered windows (e.g. when the simulator scene is reset)."""
        self._history.clear()

    def extract(self, asset_id: str, buffer: list[TelemetryMessage], hours_since_service: float) -> FeatureWindow:
        msgs = sorted((m for m in buffer if m.asset_id == asset_id), key=lambda m: m.ts)
        if not msgs:
            raise ValueError(f"no telemetry for {asset_id} in the buffer")
        base = baseline_for(self.baselines, asset_id)
        good = [m for m in msgs if m.quality not in _SKIP_QUALITY] or msgs
        running = [m for m in good if m.state in (MachineState.RUN, "run")]
        use = running or good

        def col(sig: Signal) -> list[float]:
            return [x for x in (_value(m.values, sig) for m in use) if x is not None]

        rms = col(Signal.VIBRATION_RMS)
        rms_mean = float(np.mean(rms)) if rms else base["vibration_rms"]
        peak = col(Signal.VIBRATION_PEAK)
        temp = col(Signal.BEARING_TEMP)
        temp_mean = float(np.mean(temp)) if temp else base["bearing_temp"]
        window_end = msgs[-1].ts
        rms_6h, temp_6h = self._six_hours_ago(asset_id, window_end, base)
        feats = assemble(
            rms_mean=rms_mean,
            peak_max=float(np.max(peak)) if peak else rms_mean * base["vibration_peak"] / base["vibration_rms"],
            kurt_mean=_mean(col(Signal.VIBRATION_KURTOSIS), base["vibration_kurtosis"]),
            temp_mean=temp_mean,
            current_mean=_mean(col(Signal.MOTOR_CURRENT), base["motor_current"]),
            power_mean=_mean(col(Signal.POWER_KW), base["power_kw"]),
            rpm_mean=_mean(col(Signal.SPINDLE_RPM), base["spindle_rpm"]),
            rms_6h=rms_6h, temp_6h=temp_6h, base_power=base["power_kw"],
            hours_since_service=hours_since_service,
        )
        if running:
            h = self._history.setdefault(asset_id, deque())
            h.append((window_end, rms_mean, temp_mean))
            while h and window_end - h[0][0] > timedelta(hours=TREND_HOURS) + 2 * TREND_TOLERANCE:
                h.popleft()
        return FeatureWindow(asset_id=asset_id, window_end=window_end, window_minutes=WINDOW_MINUTES, features=feats)

    def _six_hours_ago(self, asset_id: str, window_end: datetime, base: dict) -> tuple[float, float]:
        target = window_end - timedelta(hours=TREND_HOURS)
        best = None
        for ts, rms, temp in self._history.get(asset_id, ()):
            d = abs(ts - target)
            if d <= TREND_TOLERANCE and (best is None or d < best[0]):
                best = (d, rms, temp)
        if best is None:
            return base["vibration_rms"], base["bearing_temp"]
        return best[1], best[2]


def _mean(xs: list[float], default: float) -> float:
    return float(np.mean(xs)) if xs else float(default)


def assemble(*, rms_mean, peak_max, kurt_mean, temp_mean, current_mean, power_mean, rpm_mean, rms_6h, temp_6h,
             base_power, hours_since_service) -> dict[str, float]:
    """The FEATURES dict, in contract order. Used by both the live and the offline path."""
    f = {
        "vib_rms_mean": rms_mean,
        "vib_rms_trend_6h": rms_mean / rms_6h - 1 if rms_6h > 0 else 0.0,
        "vib_peak_max": peak_max,
        "vib_kurtosis_mean": kurt_mean,
        "bearing_temp_mean": temp_mean,
        "bearing_temp_trend_6h": temp_mean - temp_6h,
        "motor_current_mean": current_mean,
        "power_kw_vs_baseline": power_mean / base_power - 1 if base_power > 0 else 0.0,
        "spindle_rpm_mean": rpm_mean,
        "hours_since_service": float(hours_since_service),
    }
    assert list(f) == FEATURE_NAMES, "feature keys must match the contract"
    return {k: round(float(v), 6) for k, v in f.items()}


# --------------------------------------------------------------------------------------------- offline
def window_features(df: pd.DataFrame, baselines: dict) -> pd.DataFrame:
    """10-minute windows for ONE machine's 1-minute history (columns as in data/train.parquet).

    Each window covers (t-10 min, t] and is labelled at t (minute % 10 == 0). Returns the FEATURES columns plus
    window_end, running (>= 5 running minutes) and the label columns of the window's last minute.
    """
    aid = df["asset_id"].iat[0]
    base = baseline_for(baselines, aid)
    d = df.sort_values("ts").reset_index(drop=True)
    win = d["ts"].dt.ceil(f"{WINDOW_MINUTES}min")
    run = d["state"].eq("run")
    g = d.assign(_w=win, _run=run)
    # means over running minutes when the window has any, else over all minutes (same rule as extract())
    runs = g[g["_run"]].groupby("_w")
    alls = g.groupby("_w")
    agg = {"vibration_rms": "mean", "vibration_peak": "max", "vibration_kurtosis": "mean", "bearing_temp": "mean",
           "motor_current": "mean", "power_kw": "mean", "spindle_rpm": "mean"}
    a_run, a_all = runs.agg(agg), alls.agg(agg)
    n_run = g.groupby("_w")["_run"].sum()
    w = a_all.copy()
    has_run = n_run.reindex(w.index).fillna(0) > 0
    w.loc[has_run, :] = a_run.reindex(w.index[has_run]).values
    last = alls.tail(1).set_index("_w")
    full = alls.size() == WINDOW_MINUTES
    w = w[full.reindex(w.index).values]
    running = (n_run.reindex(w.index) >= 5).to_numpy()
    # "6 h ago": the window exactly 6 h earlier, if the machine was running then; else the machine's normal level
    idx = pd.DatetimeIndex(w.index)
    prev_t = idx - pd.Timedelta(hours=TREND_HOURS)
    has_run_all = pd.Series(n_run.reindex(w.index).to_numpy() > 0, index=idx)
    prev_ok = has_run_all.reindex(prev_t).fillna(False).to_numpy().astype(bool)
    rms_prev = w["vibration_rms"].reindex(prev_t).to_numpy()
    temp_prev = w["bearing_temp"].reindex(prev_t).to_numpy()
    rms_6h = np.where(prev_ok, rms_prev, base["vibration_rms"])
    temp_6h = np.where(prev_ok, temp_prev, base["bearing_temp"])
    out = pd.DataFrame({
        "vib_rms_mean": w["vibration_rms"].to_numpy(),
        "vib_rms_trend_6h": w["vibration_rms"].to_numpy() / rms_6h - 1,
        "vib_peak_max": w["vibration_peak"].to_numpy(),
        "vib_kurtosis_mean": w["vibration_kurtosis"].to_numpy(),
        "bearing_temp_mean": w["bearing_temp"].to_numpy(),
        "bearing_temp_trend_6h": w["bearing_temp"].to_numpy() - temp_6h,
        "motor_current_mean": w["motor_current"].to_numpy(),
        "power_kw_vs_baseline": w["power_kw"].to_numpy() / base["power_kw"] - 1,
        "spindle_rpm_mean": w["spindle_rpm"].to_numpy(),
        "hours_since_service": last.loc[w.index, "hours_since_service"].to_numpy(dtype=float),
    }).round(6)
    assert list(out.columns) == FEATURE_NAMES
    out.insert(0, "window_end", idx)
    out.insert(0, "asset_id", aid)
    out["running"] = running
    for c in ("failure_within_72h", "hours_to_failure", "failure_mode", "failure_id", "in_story", "asset_type",
              "state"):
        if c in last.columns:
            out[c] = last.loc[w.index, c].to_numpy()
    return out.reset_index(drop=True)


def compute_baselines(data: pd.DataFrame) -> dict:
    """Normal level per machine: median over running minutes with no breakdown in the next 7 days."""
    ok = data["state"].eq("run") & (data["hours_to_failure"].isna() | (data["hours_to_failure"] > 168)) \
        & ~data["in_story"]
    med = data[ok].groupby("asset_id")[BASE_KEYS].median()
    assets = {aid: {k: round(float(v), 4) for k, v in row.items()} for aid, row in med.iterrows()}
    default = {k: round(float(v), 4) for k, v in data[ok][BASE_KEYS].median().items()}
    return {"assets": assets, "default": default,
            "note": "Median of running minutes with no breakdown in the next 7 days. Used for "
                    "power_kw_vs_baseline and as the '6 h ago' value before 6 h of history exist."}
