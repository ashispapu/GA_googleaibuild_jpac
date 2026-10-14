# data/ — training data (ML-1)

Not in git (see `.gitignore`). Make it on your laptop in about 15 seconds:

```bash
cd p4-ml-data
python -m data_gen.generate --only data      # or without --only to also rebuild corpus/
```

Same seed → byte-identical files on every machine. The seed is 3 (`PG_SEED=…` to try another one; see "Seed" below).

## Files

| File | What |
|---|---|
| `train.parquet` | 20 machines × 90 days × 1 row per minute = 2,592,000 rows (~45 MB, zstd) |
| `failure_events.csv` | Every planted breakdown (48), plus EQ-3012's story breakdown marked `story=True` |
| `service_events.csv` | Preventive services inside the 90 days (empty with seed 3: every machine had a repair, and a repair resets its service counter) |

## Columns of `train.parquet`

| Column | Meaning |
|---|---|
| `ts` | Minute, **UTC**. 2026-07-15 21:41 → 2026-10-13 21:40 (the alert time, C9) |
| `plant_id`, `line_id`, `station_id`, `asset_id`, `asset_type` | From `fixtures/story/plants.json` (contract ID formats) |
| `state` | `run` / `idle` / `fault` / `stopped` (contract `MachineState`) |
| `speed_pct` | Line speed (100 = normal, 70–90 during slow-downs) |
| `vibration_rms`, `vibration_peak`, `vibration_kurtosis`, `bearing_temp`, `motor_current`, `power_kw`, `spindle_rpm` | The 7 signals in `config/tag_map.yaml`, same names as `schemas.ot.Signal`. `vibration_peak` is the highest peak in that minute |
| `hours_since_service` | Running hours since the last maintenance (service **or** repair) |
| `failure_within_72h` | **The label**: 1 if this machine breaks down in the next 72 h |
| `hours_to_failure` | Hours until the next breakdown (empty if none ahead) |
| `failure_mode`, `failure_id` | Type and id of that breakdown, when it is within 72 h |
| `in_story` | EQ-3012's last 3 days (the demo). `ml/train.py` never trains on these rows |

## What happens in the 90 days

- **The plant runs 24/7.** Every machine stops for cleaning on Sundays from 00:00 to 03:00 UTC.
- **48 planted breakdowns:** 24 bearing wear, 13 misalignment, 11 overheating. Each gets worse first, then the machine stops (`fault`) and is repaired (`stopped`). 8 of them are "subtle" (weak warning signs), so the model can't catch everything.
  - **Bearing wear** follows the EQ-3012 story shape: slow rise for days, a fast step of a few hours (kurtosis and temperature jump), then a slow climb that crosses about 2.1× normal around 40 h before the breakdown, then a final surge. One in four bearings sits on a plateau longer (fails 3–5 days after the step).
  - **Misalignment:** vibration rises steadily, kurtosis stays normal.
  - **Overheating:** temperature and power rise, vibration barely changes.
- **Harmless look-alikes**, so the model learns to stay calm:
  - heavier cutting loads, a few hours long
  - "scares" that clear by themselves (1–2 days)
  - line slow-downs to 70–90 %
  - temperature bumps after greasing
  - each machine's normal level drifting slowly and shifting after each repair
- **EQ-3012 (the demo spindle):**
  - normal (about 2.1 mm/s) for 87 days, then the story from `data_gen/story.py`: 3.2 mm/s six hours before the alert and 4.3 mm/s at the alert (+34 % in 6 h), kurtosis 5.1, bearing +8.6 °C and power +8 %
  - 3,150 h since its last service (WO-2026-0091)
  - the old rule (4.5 mm/s for 30 min) has **not** fired. `python -m data_gen.generate` checks all of this.

## Seed

The seed only changes the random details: which machine breaks when, the noise, and the harmless episodes. All seeds pass the checks. The **held-out numbers do move between seeds**, because 90 days of 20 machines only gives about 45 breakdowns. Seeds 1–8 gave:

- precision 0.75–0.87
- recall 0.83–0.96
- hours earlier than the rule: 8–54

The rule only fires on some breakdowns, so that last number is the noisiest. Seed 3 is the default because its numbers land closest to the story (31 h earlier).
