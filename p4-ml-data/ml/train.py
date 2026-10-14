"""Train the models (ML-3) and measure them (ML-4).

    python -m ml.train                       # from p4-ml-data/, after python -m data_gen.generate
    python -m ml.train --data data/train.parquet --out models

Writes models/<version>/ (C5 artifacts + a few extras) and points models/latest at it.
Metrics are measured on held-out time blocks (4 blocks of ~22 days; each block is predicted by a model
that never saw it, with 72 h removed on both sides so labels can't leak), then one final model is fitted on
all the data. EQ-3012's demo days are never used for training; they are scored at the end as a check.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import IsolationForest
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

P4 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P4))
sys.path.insert(0, str(P4.parent))

from ml.features import FEATURE_NAMES, compute_baselines, window_features  # noqa: E402

VERSION = "fp-xgb-1.0.0"
ANOMALY_VERSION = "an-if-1.0.0"
ALERT_AT = 0.7            # C0/C5: risk >= 0.7 is "critical" and raises an alert
EPISODE_GAP_H = 12        # alerts closer than this belong to the same warning
MODES = ["bearing_wear", "misalignment", "overheating"]
RULE_RMS, RULE_MIN = 4.5, 30
SEED = 42

REAL_WITHIN_H = 168      # a warning "was real" if that machine broke down within 7 days after it
# Risk may only go UP when these go up (keeps the model from learning odd per-machine shortcuts)
MONOTONE = {"vib_rms_trend_6h": 1, "vib_kurtosis_mean": 1, "bearing_temp_trend_6h": 1, "power_kw_vs_baseline": 1,
            "hours_since_service": 1}
# Which features may be combined in one tree: "how the machine runs" levels, "what is changing" signs, and
# service hours on its own. Without this the model learns shortcuts like "machine X at 3,000 h = trouble".
_LEVELS = ["vib_rms_mean", "vib_peak_max", "bearing_temp_mean", "motor_current_mean", "spindle_rpm_mean",
           "power_kw_vs_baseline"]
_CHANGES = ["vib_rms_trend_6h", "vib_kurtosis_mean", "bearing_temp_trend_6h", "power_kw_vs_baseline", "vib_rms_mean",
            "vib_peak_max", "bearing_temp_mean"]
INTERACTIONS = [[FEATURE_NAMES.index(f) for f in g] for g in (_LEVELS, _CHANGES, ["hours_since_service"])]
CLF_PARAMS = dict(n_estimators=400, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                  min_child_weight=30, reg_lambda=2.0, tree_method="hist", random_state=SEED, n_jobs=2,
                  eval_metric="logloss",
                  monotone_constraints="(" + ",".join(str(MONOTONE.get(f, 0)) for f in FEATURE_NAMES) + ")",
                  interaction_constraints=json.dumps(INTERACTIONS))


def xgb_blob(model) -> dict:
    """An XGBoost model as XGBoost's version-independent UBJSON model bytes (not a pickle of the object)."""
    return {"format": "xgboost-ubj", "xgboost_version": xgb.__version__,
            "model": bytes(model.get_booster().save_raw(raw_format="ubj"))}


def iforest_blob(iso) -> dict:
    """The Isolation Forest as plain arrays (tree structure per tree), scored by ml.serving without scikit-learn."""
    trees = []
    for est, feats in zip(iso.estimators_, iso.estimators_features_):
        t = est.tree_
        feats = np.asarray(feats)
        trees.append({"left": t.children_left.tolist(), "right": t.children_right.tolist(),
                      "feature": [int(feats[f]) if f >= 0 else -1 for f in t.feature],
                      "threshold": t.threshold.tolist(), "n": t.n_node_samples.tolist()})
    return {"format": "isolation-forest", "max_samples": int(iso.max_samples_), "trees": trees}


def write_manifest(out: Path) -> None:
    """Size + SHA-256 of every binary file, so load_model can tell a damaged or half-copied file apart."""
    import hashlib
    files = sorted(p for p in out.iterdir() if p.suffix in (".joblib", ".npy"))
    (out / "manifest.json").write_text(json.dumps(
        {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files},
        indent=2))


def build_windows(data: pd.DataFrame, baselines: dict) -> pd.DataFrame:
    parts = [window_features(g, baselines) for _, g in data.groupby("asset_id", sort=True)]
    return pd.concat(parts, ignore_index=True)


def rule_fire_times(data: pd.DataFrame) -> dict[str, np.ndarray]:
    """Classic rule: RMS > 4.5 mm/s for 30 consecutive running minutes -> fire time(s) per machine."""
    out = {}
    for aid, g in data.groupby("asset_id", sort=False):
        ex = ((g["vibration_rms"].to_numpy() > RULE_RMS) & g["state"].eq("run").to_numpy()).astype(np.int32)
        # length of the current run of exceedances at each minute
        idx = np.arange(len(ex))
        last_zero = np.maximum.accumulate(np.where(ex == 0, idx, -1))
        run_len = idx - last_zero
        out[aid] = g["ts"].to_numpy()[run_len == RULE_MIN]
    return out


def evaluate(win: pd.DataFrame, risk: np.ndarray, failures: pd.DataFrame, rules: dict) -> dict:
    """Event-level numbers the UI shows: breakdowns caught, warnings that were real, hours earlier than the rule."""
    w = win.assign(risk=risk)
    caught, gains, missed = 0, [], []
    true_ep, all_ep = 0, 0
    for aid, g in w.groupby("asset_id"):
        t = g["window_end"].to_numpy()
        hot = g["risk"].to_numpy() >= ALERT_AT
        fs = failures[failures["asset_id"] == aid]
        # warnings (episodes)
        starts, last = [], None
        for ti in t[hot]:
            if last is None or (ti - last) > np.timedelta64(EPISODE_GAP_H, "h"):
                starts.append(ti)
            last = ti
        for s in starts:
            all_ep += 1
            if any(np.timedelta64(0, "h") < (np.datetime64(f) - s) <= np.timedelta64(REAL_WITHIN_H, "h")
                   for f in fs["failure_at"].to_numpy()):
                true_ep += 1
        # breakdowns
        for _, f in fs.iterrows():
            fa = np.datetime64(f["failure_at"])
            sel = hot & (t < fa) & (t >= fa - np.timedelta64(72, "h"))
            if not sel.any():
                missed.append(f["failure_id"])
                continue
            caught += 1
            ml_t = t[sel][0]
            rf = rules.get(aid, np.array([], dtype="datetime64[ns]"))
            rf = rf[(rf < fa) & (rf > np.datetime64(f["degradation_start"]) - np.timedelta64(6, "h"))]
            if len(rf):
                gains.append((rf[0] - ml_t) / np.timedelta64(1, "h"))
    n = len(failures)
    precision = true_ep / all_ep if all_ep else 0.0
    recall = caught / n if n else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "lead_time_gain_hours": round(float(np.median(gains)), 1) if gains else 0.0,
            "breakdowns_evaluated": float(n), "breakdowns_caught": float(caught), "warnings": float(all_ep),
            "warnings_real": float(true_ep), "lead_time_pairs": float(len(gains)),
            "_missed": missed}


def blocked_folds(win: pd.DataFrame, k: int = 4, purge_h: int = 72):
    t = win["window_end"]
    edges = pd.date_range(t.min(), t.max() + pd.Timedelta(minutes=1), periods=k + 1)
    for i in range(k):
        lo, hi = edges[i], edges[i + 1]
        test = (t >= lo) & (t < hi)
        train = (t < lo - pd.Timedelta(hours=purge_h)) | (t >= hi + pd.Timedelta(hours=purge_h))
        yield train.to_numpy(), test.to_numpy()


def fit_clf(X, y):
    m = xgb.XGBClassifier(**CLF_PARAMS)
    m.fit(X, y)
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(P4 / "data" / "train.parquet"))
    ap.add_argument("--events", default=str(P4 / "data" / "failure_events.csv"))
    ap.add_argument("--out", default=str(P4 / "models"))
    args = ap.parse_args(argv)
    t0 = time.time()

    data = pd.read_parquet(args.data)
    events = pd.read_csv(args.events, parse_dates=["degradation_start", "failure_at"])
    baselines = compute_baselines(data)
    win = build_windows(data, baselines)
    usable = win["running"] & ~win["in_story"].astype(bool)
    W = win[usable].reset_index(drop=True)
    X, y = W[FEATURE_NAMES].to_numpy(np.float32), W["failure_within_72h"].to_numpy()
    print(f"windows: {len(W):,} (positive {y.mean():.1%}) in {time.time() - t0:.0f} s")

    failures = events[~events["story"]].copy()
    failures["failure_at"] = failures["failure_at"].dt.tz_convert(None)
    failures["degradation_start"] = failures["degradation_start"].dt.tz_convert(None)
    W_eval = W.assign(window_end=W["window_end"].dt.tz_convert(None))
    rules = {k: v for k, v in rule_fire_times(data.assign(ts=data["ts"].dt.tz_convert(None))).items()}

    # ---------------- held-out measurement ----------------
    # raw risk per held-out block, then a calibration step (isotonic) so "0.8" means ~80 % of such windows
    # really broke down within 72 h. The calibration of each block is fitted on the OTHER blocks only.
    metrics: dict = {}
    folds = list(blocked_folds(W))
    oof = np.full(len(W), np.nan)
    for tr, te in folds:
        oof[te] = fit_clf(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    ok = ~np.isnan(oof)
    cal_oof = np.full(len(W), np.nan)
    for _, te in folds:
        other = ok & ~te
        iso_k = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof[other], y[other])
        cal_oof[te] = iso_k.predict(oof[te])
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof[ok], y[ok])
    ev = evaluate(W_eval[ok], cal_oof[ok], failures, rules)
    missed = ev.pop("_missed")
    metrics = {**ev, "roc_auc": round(float(roc_auc_score(y[ok], cal_oof[ok])), 3)}
    print("held-out:", json.dumps(metrics), "missed:", missed)

    # ---------------- final models on all usable windows ----------------
    clf = fit_clf(X, y)
    pos = y == 1
    mode_y = W.loc[pos, "failure_mode"].map({m: i for i, m in enumerate(MODES)}).to_numpy()
    plain = {k: v for k, v in CLF_PARAMS.items() if k not in ("monotone_constraints", "interaction_constraints")}
    mode_clf = xgb.XGBClassifier(**{**plain, "n_estimators": 200, "max_depth": 4, "eval_metric": "mlogloss"},
                                 objective="multi:softprob", num_class=len(MODES))
    mode_clf.fit(X[pos], mode_y)
    deg = W["hours_to_failure"].notna() & (W["hours_to_failure"] <= 240)
    rul = xgb.XGBRegressor(**{**plain, "max_depth": 4, "eval_metric": "rmse"})
    rul.fit(X[deg.to_numpy()], W.loc[deg, "hours_to_failure"].to_numpy())

    healthy = (W["hours_to_failure"].isna() | (W["hours_to_failure"] > 168)).to_numpy()
    iso = IsolationForest(n_estimators=100, max_samples=512, random_state=SEED, n_jobs=1).fit(X[healthy])
    raw = -iso.score_samples(X[healthy])
    q50, q99 = np.quantile(raw, [0.5, 0.99])
    anomaly_cfg = {"q50": float(q50), "q99": float(q99), "scale": float((q99 - q50) / 3)}
    an = 1 / (1 + np.exp(-((-iso.score_samples(X)) - q99) / anomaly_cfg["scale"]))
    an_hit = an >= 0.5
    tp = int((an_hit & (y == 1)).sum())
    an_metrics = {"precision": round(tp / max(an_hit.sum(), 1), 3), "recall": round(tp / max((y == 1).sum(), 1), 3)}
    an_metrics["f1"] = round(2 * an_metrics["precision"] * an_metrics["recall"]
                             / max(an_metrics["precision"] + an_metrics["recall"], 1e-9), 3)

    rng = np.random.default_rng(SEED)
    bg = X[healthy][rng.choice(healthy.sum(), 200, replace=False)]

    # ---------------- write artifacts ----------------
    out = Path(args.out) / VERSION
    out.mkdir(parents=True, exist_ok=True)
    # Every .joblib holds only plain Python values (bytes, lists, numbers), never library objects, so it loads
    # with any XGBoost / scikit-learn / numpy version. XGBoost models use XGBoost's own portable model format.
    joblib.dump(xgb_blob(clf), out / "failure_clf.joblib")
    joblib.dump({"format": "isotonic", "x": calibrator.X_thresholds_.tolist(), "y": calibrator.y_thresholds_.tolist()},
                out / "calibrator.joblib")
    joblib.dump({**iforest_blob(iso), **anomaly_cfg}, out / "anomaly.joblib")
    joblib.dump({**xgb_blob(mode_clf), "classes": MODES}, out / "mode_clf.joblib")
    joblib.dump(xgb_blob(rul), out / "rul_reg.joblib")
    np.save(out / "shap_background.npy", bg.astype(np.float32))
    (out / "feature_list.json").write_text(json.dumps(FEATURE_NAMES, indent=2))
    (out / "asset_baselines.json").write_text(json.dumps(baselines, indent=2))
    public = {k: v for k, v in metrics.items() if k in ("precision", "recall", "f1", "roc_auc", "lead_time_gain_hours")}
    (out / "metrics.json").write_text(json.dumps(public, indent=2))
    meta = {"version": VERSION, "anomaly_version": ANOMALY_VERSION,
            "trained_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "training_windows": int(len(W)), "positive_windows": int(y.sum()),
            "details": {k: v for k, v in metrics.items() if k not in public and not k.startswith("_")},
            "anomaly_metrics": an_metrics, "alert_threshold": ALERT_AT}
    (out / "model_meta.json").write_text(json.dumps(meta, indent=2))
    write_manifest(out)
    latest = Path(args.out) / "latest"
    if latest.is_symlink() or latest.is_file():
        latest.unlink()
    if not latest.exists():
        latest.write_text(VERSION + "\n")            # plain text pointer: works on Windows too
    print(f"wrote {out} in {time.time() - t0:.0f} s")

    # ---------------- the demo window ----------------
    story = win[win["in_story"].astype(bool) & win["running"]]
    if len(story):
        p = calibrator.predict(clf.predict_proba(story[FEATURE_NAMES].to_numpy(np.float32))[:, 1])
        last = story.iloc[-1]
        first = story["window_end"].iloc[int(np.argmax(p >= ALERT_AT))] if (p >= ALERT_AT).any() else None
        metrics["_story"] = {"risk_at_alert": round(float(p[-1]), 3), "first_alert": str(first)}
        print(f"EQ-3012 at the alert: risk {p[-1]:.2f}, rms {last['vib_rms_mean']:.2f} mm/s, "
              f"trend {last['vib_rms_trend_6h']:+.2f}; first >= 0.7 at {first}")
    return metrics


if __name__ == "__main__":
    main()
