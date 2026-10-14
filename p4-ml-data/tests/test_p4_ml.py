"""P4 tests: features (ML-2), model (ML-3/ML-4) and corpus (ML-6). Run with `make test` or
`pytest -q p4-ml-data/tests`. They use only committed files (fixtures, models, corpus), not data/."""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

P4 = Path(__file__).resolve().parents[1]
REPO = P4.parent
for p in (str(REPO), str(P4)):
    if p not in sys.path:
        sys.path.insert(0, p)

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("xgboost")
pytest.importorskip("sklearn")

from plantguard_contracts.schemas.maintenance import WorkOrder  # noqa: E402
from plantguard_contracts.schemas.ml import FEATURES, AssetHealth, ModelInfo  # noqa: E402
from plantguard_contracts.schemas.ot import SignalSeries, TelemetryMessage  # noqa: E402
from pydantic import TypeAdapter  # noqa: E402

from ml.features import FEATURE_NAMES, WindowFeatureExtractor, window_features  # noqa: E402
from ml.serving import load_model  # noqa: E402

FX = REPO / "fixtures" / "story"
MODEL = P4 / "models" / "latest"
ALERT = datetime(2026, 10, 13, 21, 40, tzinfo=timezone.utc)
CALM = {"vibration_rms": 2.1, "vibration_peak": 5.8, "vibration_kurtosis": 3.0, "bearing_temp": 58.0,
        "motor_current": 21.0, "power_kw": 11.0, "spindle_rpm": 12000.0}


@pytest.fixture(scope="module")
def model():
    return load_model(MODEL)


def fixture_messages():
    """EQ-3012 chart data from the story fixture as telemetry messages (one per 5-minute point)."""
    series = TypeAdapter(list[SignalSeries]).validate_json((FX / "signals_EQ-3012.json").read_text())
    by_ts: dict = {}
    for s in series:
        for p in s.points:
            by_ts.setdefault(p.ts, {})[s.signal] = p.value
    return [TelemetryMessage(asset_id="EQ-3012", line_id="L3", ts=t, state="run", values=v) for t, v in sorted(by_ts.items())]


def story_window(model):
    """Feed the extractor 6 h of fixture history, then the last 10 minutes before the alert."""
    ex = WindowFeatureExtractor(model.baselines)
    msgs = fixture_messages()
    fw = None
    for i in range(1, len(msgs)):
        if msgs[i].ts > ALERT - timedelta(hours=6, minutes=20):
            fw = ex.extract("EQ-3012", msgs[max(0, i - 1):i + 1], 3150.0)    # 10 min = two 5-minute points
    return fw


def live(values, seconds=600, extractor=None, model=None):
    ex = extractor or WindowFeatureExtractor(model.baselines)
    msgs = [TelemetryMessage(asset_id="EQ-3012", line_id="L3", ts=ALERT - timedelta(seconds=seconds - i), state="run",
                             values=values) for i in range(seconds)]
    return ex.extract("EQ-3012", msgs, 3150.0)


# ------------------------------------------------------------------ ML-2 features
def test_feature_keys_equal_contract(model):
    fw = live(CALM, model=model)
    assert list(fw.features) == list(FEATURES) == FEATURE_NAMES
    assert fw.window_minutes == 10


def test_features_on_fixture_signals(model):
    f = story_window(model).features
    assert 4.1 <= f["vib_rms_mean"] <= 4.5                    # C9: 4.3 mm/s
    assert 0.25 <= f["vib_rms_trend_6h"] <= 0.45              # +34 % in 6 h
    assert 7.0 <= f["bearing_temp_trend_6h"] <= 10.0          # +8.6 degC in 6 h
    assert 0.05 <= f["power_kw_vs_baseline"] <= 0.11          # +8 % vs baseline
    assert f["vib_kurtosis_mean"] > 4.5


def test_trend_falls_back_to_normal_level_without_history(model):
    f = live({**CALM, "vibration_rms": 4.2}, model=model).features
    assert abs(f["vib_rms_trend_6h"] - (4.2 / model.baselines["assets"]["EQ-3012"]["vibration_rms"] - 1)) < 1e-3


def test_live_and_offline_paths_agree(model):
    rng = np.random.default_rng(0)
    ts = pd.date_range(ALERT - timedelta(hours=7), ALERT, freq="1min", inclusive="right")
    n = len(ts)
    ramp = np.linspace(0, 1, n) ** 2
    df = pd.DataFrame({"ts": ts, "asset_id": "EQ-3012", "line_id": "L3", "state": "run",
                       "vibration_rms": 2.1 + 2.2 * ramp + rng.normal(0, 0.05, n),
                       "vibration_peak": 5.8 + 6 * ramp, "vibration_kurtosis": 3 + 2 * ramp,
                       "bearing_temp": 58 + 9 * ramp, "motor_current": 21 + 1.7 * ramp, "power_kw": 11 + 0.9 * ramp,
                       "spindle_rpm": 12000.0, "hours_since_service": 3150.0})
    off = window_features(df, model.baselines).set_index("window_end")
    ex = WindowFeatureExtractor(model.baselines)
    worst = 0.0
    for end in off.index:
        buf = df[(df["ts"] > end - timedelta(minutes=10)) & (df["ts"] <= end)]
        msgs = [TelemetryMessage(asset_id="EQ-3012", line_id="L3", ts=r.ts.to_pydatetime(), state="run",
                                 values={k: float(getattr(r, k)) for k in CALM}) for r in buf.itertuples()]
        f = ex.extract("EQ-3012", msgs, 3150.0).features
        worst = max(worst, max(abs(f[k] - float(off.loc[end, k])) for k in FEATURE_NAMES))
    assert worst < 1e-3


# ------------------------------------------------------------------ ML-3 model
def test_story_window_is_critical_and_rule_not_breached(model):
    h = model.score(story_window(model))
    assert isinstance(h, AssetHealth)
    assert h.risk_72h >= 0.7 and h.health.value == "critical"
    assert h.rule_threshold_breached is False                 # the demo moment: ML warns, the old rule doesn't
    assert h.predicted_failure_mode == "bearing_wear"
    assert len(h.top_features) == 5 and {f.name for f in h.top_features} <= set(FEATURES)
    assert h.model_version == "fp-xgb-1.0.0"


def test_live_scenes(model):
    assert model.score(live(CALM, model=model)).risk_72h < 0.3                     # calm scene
    warn = {"vibration_rms": 4.3, "vibration_peak": 13.4, "vibration_kurtosis": 5.1, "bearing_temp": 67.0,
            "motor_current": 22.7, "power_kw": 11.88, "spindle_rpm": 12000.0}      # end of the C6 warning ramp
    h = model.score(live(warn, model=model))
    assert h.risk_72h >= 0.7 and not h.rule_threshold_breached


def test_score_is_fast_and_deterministic(model):
    fw = story_window(model)
    first = model.score(fw)
    t = time.perf_counter()
    for _ in range(20):
        assert model.score(fw) == first
    assert (time.perf_counter() - t) / 20 < 0.05


def test_info_and_metrics(model):
    infos = model.info()
    assert [i.kind for i in infos] == ["failure_classifier", "anomaly"]
    for i in infos:
        ModelInfo.model_validate(i.model_dump())
    m = infos[0].metrics
    assert m["lead_time_gain_hours"] > 0                       # C5 requirement
    assert {"precision", "recall", "f1", "roc_auc"} <= set(m)
    assert json.loads((P4 / "models" / "fp-xgb-1.0.0" / "feature_list.json").read_text()) == list(FEATURES)


# ------------------------------------------------------------------ ML-6 corpus
def test_work_orders():
    wos = TypeAdapter(list[WorkOrder]).validate_json((P4 / "corpus" / "work_orders" / "work_orders.json").read_text())
    assert 190 <= len(wos) <= 210
    ids = [w.id for w in wos]
    assert len(set(ids)) == len(ids) and "WO-2026-0153" not in ids
    fixture = json.loads((FX / "history_EQ-3012.json").read_text())
    got = {w.id: w.model_dump(mode="json") for w in wos}
    for w in fixture:
        assert got[w["id"]] == w


def test_manuals():
    pypdf = pytest.importorskip("pypdf")
    import re
    norm = lambda t: re.sub(r"\s+", " ", t or "")
    man = P4 / "corpus" / "manuals"
    pdfs = sorted(man.glob("*.pdf"))
    assert 3 <= len(pdfs) <= 5
    spindle = next(p for p in pdfs if p.name.startswith("MAN-SPINDLE-200"))
    reader = pypdf.PdfReader(str(spindle))
    for c in json.loads((FX / "docsearch_bearing.json").read_text())["chunks"]:
        if c["doc_id"] == "MAN-SPINDLE-200":
            assert norm(c["text"]) in norm(reader.pages[c["page"] - 1].extract_text()), c["section"]
    assert not any("hidden" in p.name for p in pdfs)          # the poisoned copy lives in fixtures/security
