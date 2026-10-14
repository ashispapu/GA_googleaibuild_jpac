"""Synthetic data for PlantGuard (ML-1 + ML-6).

    cd p4-ml-data && python -m data_gen.generate          # ~15 s, same seed -> same files every time
    python -m data_gen.generate --only data                # just the training data
    python -m data_gen.generate --only corpus              # just manuals + work orders

Writes:
  data/train.parquet          20 machines x 90 days, one row per minute, the 7 tag_map signals + labels
  data/failure_events.csv     every planted breakdown (and the EQ-3012 story one, marked story=True)
  data/service_events.csv     preventive services in the 90 days
  corpus/work_orders/work_orders.json      ~200 past work orders (WorkOrder schema), incl. WO-2024-0871
  corpus/manuals/*.pdf + index.json        5 manuals, MAN-SPINDLE-200 sections 7.4 (p. 58) and 7.6 (p. 61)
  ../fixtures/security/                    a manual with a hidden instruction, for the security demo only

The machine list comes from fixtures/story/plants.json and the EQ-3012 story from data_gen/story.py,
so run `make export` first if you changed build_fixtures.py.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

P4 = Path(__file__).resolve().parents[1]
REPO = P4.parent
for p in (str(REPO), str(P4)):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_gen import checks, plant, signals, timeline  # noqa: E402

DATA = P4 / "data"


def build_data(assets, tl) -> pd.DataFrame:
    frames = []
    for a in assets:
        df = signals.build_asset(a, tl)
        frames.append(signals.labels(df, a, tl))
    data = pd.concat(frames, ignore_index=True)
    for c in ("plant_id", "line_id", "station_id", "asset_id", "asset_type", "state", "failure_mode", "failure_id"):
        data[c] = data[c].astype("category")
    return data


def events_frames(assets, tl, wo_ids: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    where = {a.id: a for a in assets}
    fe = pd.DataFrame([{
        "failure_id": f.id, "asset_id": f.asset_id, "asset_type": where[f.asset_id].type,
        "plant_id": where[f.asset_id].plant_id, "line_id": where[f.asset_id].line_id, "failure_mode": f.mode,
        "degradation_start": f.degradation_start, "failure_at": f.failure_at,
        "repair_start": f.repair_start, "repair_end": f.repair_end, "subtle": f.subtle, "story": f.story,
        "work_order_id": wo_ids.get(f.id, ""),
    } for f in sorted(tl.failures, key=lambda f: f.failure_at)])
    se = pd.DataFrame([{"asset_id": s.asset_id, "start": s.start, "end": s.end,
                        "work_order_id": wo_ids.get(("svc", s.asset_id, s.start), "")}
                       for s in sorted(tl.services, key=lambda s: s.start)])
    return fe, se


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["data", "corpus"], default=None)
    args = ap.parse_args(argv)
    t0 = time.time()
    assets = plant.load_assets()
    tl = timeline.build(assets)
    data = build_data(assets, tl)            # also schedules the preventive services into tl
    print(f"{len(assets)} machines, {sum(not f.story for f in tl.failures)} breakdowns, "
          f"{len(tl.services)} services, {len(data):,} rows ({time.time() - t0:.0f} s)")

    from data_gen import work_orders            # needs the services, so after build_data
    wos, wo_ids = work_orders.build(assets, tl)
    fe, se = events_frames(assets, tl, wo_ids)

    if args.only in (None, "data"):
        DATA.mkdir(exist_ok=True)
        data.to_parquet(DATA / "train.parquet", compression="zstd", index=False)
        fe.to_csv(DATA / "failure_events.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
        se.to_csv(DATA / "service_events.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
        print(f"data/ written ({(DATA / 'train.parquet').stat().st_size / 1e6:.0f} MB)")

    if args.only in (None, "corpus"):
        work_orders.write(wos)
        from data_gen import manuals
        manuals.write_all()
        print(f"corpus written: {len(wos)} work orders, manuals in corpus/manuals")

    problems = checks.run(data, tl, assets, wos, fe, only=args.only)
    for p in problems:
        print("CHECK FAILED:", p)
    print("all checks passed" if not problems else f"{len(problems)} check(s) failed",
          f"({time.time() - t0:.0f} s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
