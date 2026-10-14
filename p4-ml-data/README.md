# P4 — ML + Data

**Your job:** the predictive maintenance model, the fake plant (OT simulator), the documents the AI searches, and the story data everyone uses.

## Start here
1. `make test` from the repo root.
2. Read `CONTRACTS.md` sections **C5, C6, C9**.
3. You own `../fixtures/build_fixtures.py` (story numbers) and `../config/tag_map.yaml` (OT tags). They sit in the root because everyone reads them.

## Your tasks
| ID | Task | Pts | Done when |
|---|---|---|---|
| ML-1 | Synthetic data: 20 assets × 90 days, injected failures (`data_gen/generate.py`) | 4 | parquet + labels |
| ML-2 | Feature extractor producing exactly `FEATURES` (`ml/features.py`) | 2 | unit test on fixture signals |
| ML-3 | IsolationForest + XGBoost + SHAP, `load_model()` (`ml/serving.py`, `models/`) | 4 | story → risk ≥ 0.7, rule not breached |
| ML-4 | Metrics incl. `lead_time_gain_hours` | 1 | model page data |
| ML-5 | OT simulator on MQTT, reacts to scenes (`simulator/publisher.py`) | 4 | P1's gateway receives live data |
| ML-6 | RAG corpus: 3–5 manuals + ~200 work orders (`corpus/`) | 2 | handed to P2 early |
| FX-6 | Story slides + before/after visuals | 3 | 8-scene deck |

## Rules
- Feature keys must equal `plantguard_contracts/schemas/ml.py: FEATURES`.
- `score()` returns in < 50 ms and gives the same output for the same input.
- `rule_threshold_breached` stays **false** while ML already warns. That's the demo moment.
- Changing a story number? Edit `build_fixtures.py`, run `make export`, tell the team.

## You provide
C5 model (to P1) · C6 OT feed (to P1) · C9 story data + corpus (to everyone)

## What's here now

| Task | Status | Where |
|---|---|---|
| ML-1 synthetic data | done | `data_gen/` → `data/train.parquet` (not in git; `python -m data_gen.generate --only data`, ~15 s). See `data/README.md` |
| ML-2 features | done | `ml/features.py`: exactly `FEATURES`; live and offline paths agree (tested) |
| ML-3 model | done | `ml/train.py`, `ml/serving.py`, `models/fp-xgb-1.0.0/`. Story window → risk 0.98, rule not breached, ~15 ms |
| ML-4 metrics | done | `models/fp-xgb-1.0.0/metrics.json`: lead_time_gain_hours 30.8. See `models/README.md` |
| ML-5 simulator | **to do** | `simulator/publisher.py` (use `data_gen/story.py` for the EQ-3012 curve) |
| ML-6 corpus | done | `corpus/manuals/` (5 PDFs + `index.json`), `corpus/work_orders/work_orders.json` (200) |

```bash
pip install -r requirements-ml.txt          # or merge it into the root requirements.txt
cd p4-ml-data
python -m data_gen.generate                 # data/ + corpus/ + ../fixtures/security/, then runs its checks
python -m ml.train                          # models/fp-xgb-1.0.0 + models/latest
cd .. && make export && make test           # fixtures pick up the model numbers; p4-ml-data/tests run with the rest
```

**One source for each fact:**
- **Machine list:** `fixtures/story/plants.json`, built by `fixtures/build_fixtures.py`.
- **EQ-3012 story curve:** `data_gen/story.py`. `build_fixtures.py` imports it for `signals_EQ-3012.json`.
- **Model-page numbers:** `models/latest/metrics.json`. `build_fixtures.py` copies them into `models.json`.

