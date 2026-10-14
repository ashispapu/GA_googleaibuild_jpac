# Trained models (ML-3, ML-4)

`models/<version>/` holds one trained version; `models/latest` is a one-line **text file** with the version name
(`fp-xgb-1.0.0`). It is a file rather than a symlink so it works on Windows; `load_model()` accepts a folder, a symlink or this file.

```bash
cd p4-ml-data
python -m data_gen.generate --only data   # if data/ is empty
python -m ml.train                        # ~25 s: measures, trains, writes models/fp-xgb-1.0.0, updates latest
cd .. && make export                      # fixtures/story/models.json picks up the new numbers
```

## How the backend uses it (C5)

```python
import sys; sys.path.insert(0, "p4-ml-data")
from ml.features import WindowFeatureExtractor
from ml.serving import load_model

model = load_model("p4-ml-data/models/latest")        # once at startup
extractor = WindowFeatureExtractor()                  # ONE object for the whole process (it remembers the last 6 h)
window = extractor.extract(asset_id, last_10_min_of_telemetry, hours_since_service)
health = model.score(window)                          # AssetHealth, ~15 ms, same input -> same output
model.info()                                          # [ModelInfo failure_classifier, ModelInfo anomaly] for GET /ml/models
```

## Files in `fp-xgb-1.0.0/`

| File | What |
|---|---|
| `failure_clf.joblib` | XGBoost classifier: chance of a breakdown in the next 72 h |
| `calibrator.joblib` | Turns the raw score into an honest probability (fitted on held-out predictions) |
| `anomaly.joblib` | Isolation Forest trained on healthy windows → `anomaly_score` |
| `mode_clf.joblib` | Which failure: bearing_wear / misalignment / overheating |
| `rul_reg.joblib` | Hours until breakdown (`rul_hours`), used when risk ≥ 0.3 |
| `feature_list.json` | The 10 `FEATURES` keys in order (checked at load) |
| `metrics.json` | `ModelInfo.metrics` of the classifier |
| `shap_background.npy` | 200 healthy windows: the reference the explanations are measured against |
| `asset_baselines.json` | Normal level of each signal per machine (for "vs baseline" and as the "6 h ago" fallback) |
| `model_meta.json` | Version, training time, extra counts, anomaly-model metrics |

## Numbers (held-out, seed 3)

Measured on 4 time blocks of ~22 days. Each block is scored by a model that never saw it, with 72 h removed on both sides.

| Metric | Value | In plain words |
|---|---|---|
| recall | 0.896 | 43 of 48 breakdowns got a warning in the 72 h before them |
| precision | 0.83 | 44 of 53 warnings were followed by a breakdown of that machine within 7 days |
| f1 | 0.862 | |
| roc_auc | 0.857 | per 10-minute window, label "breaks down within 72 h" |
| **lead_time_gain_hours** | **30.8** | median hours the first warning came before the 4.5 mm/s rule fired (the 25 breakdowns where the rule fired at all) |

A warning is the risk crossing 0.7; crossings less than 12 h apart count as one warning.

## The demo window (EQ-3012 at the alert, built from `fixtures/story/signals_EQ-3012.json`)

The features come out as in C9: 4.29 mm/s, +34 % in 6 h, kurtosis 5.2, bearing +8.4 °C in 6 h, power +7 %, 3,150 h since service.

The model gives:

| Output | Model | C9 / fixture |
|---|---|---|
| `risk_72h` | **0.98** | 0.78 |
| `rul_hours` | **46** | 70 |
| `anomaly_score` | 0.92 | 0.91 |
| `predicted_failure_mode` | bearing_wear | bearing_wear |
| `rule_threshold_breached` | false | false |

- **Before the alert:** the risk first passes 0.7 at 17:30 UTC, during the last-6-hour rise.
- **Live warning scene** (C6 ramp, fresh extractor): 0.95.
- **Calm scene:** 0.02.

**Known difference:** the trained model is more certain (0.98) and expects the breakdown sooner (46 h) than the golden numbers (78 %, 70 h). The fixtures and the replayed agent run keep the C9 values. The real backend will show the model's own numbers.

## Notes

- **Files are version-safe.** Each `.joblib` holds plain Python values only: XGBoost models in XGBoost's own model format, the Isolation Forest and the calibration as arrays. They don't break when scikit-learn or numpy versions differ.
  - **XGBoost must be 3.4 or newer** (the version it was trained with). `load_model` refuses older versions, because they read the file but predict wrongly.
  - **`manifest.json` holds the size and SHA-256 of every file.** `load_model` stops with a clear message if a file was damaged or half-copied. `p4-ml-data/.gitattributes` marks these files as binary so git never changes them.

- **`rule_threshold_breached`:** judged on the 10-minute mean (> 4.5 mm/s), because `score()` sees one window. The full rule (30 min sustained) is what the metrics use.
- **The mock's live stream** sends only 5 of the 7 signals (no peak, no kurtosis). The extractor then uses the machine's normal values for the missing two, and the model calls it misalignment instead of bearing wear. The real simulator (ML-5) should send all 7.
- **Constraints:** the classifier may only raise risk when trends, kurtosis, power or service hours go up, and service hours can't be combined with machine-identity features. Without this it learned shortcuts like "machine X at 3,000 h = trouble".
