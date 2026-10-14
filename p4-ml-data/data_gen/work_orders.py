"""~200 past work orders (WorkOrder schema) for the AI's search, in ONE file: corpus/work_orders/work_orders.json.

Every breakdown and service in the 90-day sensor data has its work order, so what the AI reads matches the data.
Older ones (2024 to July 2026) fill in history. EQ-3012 has exactly the two work orders in
fixtures/story/history_EQ-3012.json (WO-2024-0871 and WO-2026-0091), copied word for word.

Numbering: WO-<year>-<n>, counting every work order in the plant that year. The corpus is a sample, so numbers
have gaps. 2026 numbers stop at 0152 before the alert, because the demo creates WO-2026-0153 (C9).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from plantguard_contracts.schemas.maintenance import WorkOrder

from . import story
from .plant import FIXTURES, P4, Asset
from .timeline import END, SEED, START, Timeline

OUT = P4 / "corpus" / "work_orders" / "work_orders.json"
TARGET = 200
HISTORY_FROM = pd.Timestamp("2024-01-02T00:00:00Z")
DEMO_WO = "WO-2026-0153"              # created by the demo approval: must NOT be in the corpus
# (time, number) anchors of the plant-wide counter per year; numbers between anchors are spread by time
ANCHORS = {
    2024: [("2024-01-01T00:00Z", 0), ("2024-11-05T02:00Z", 871), ("2025-01-01T00:00Z", 952)],
    2025: [("2025-01-01T00:00Z", 0), ("2026-01-01T00:00Z", 431)],
    2026: [("2026-01-01T00:00Z", 0), ("2026-06-01T09:00Z", 91), (END.isoformat(), 153)],
}

# Plant A technicians (fixtures/story/technicians.json). Plant B has no technicians in the fixtures yet,
# so its work orders leave technician_id empty.
TECH = {"bearing": ["TECH-104", "TECH-112"], "align": ["TECH-104"], "elec": ["TECH-112"], "hyd": ["TECH-120"]}

ROOT = {
    ("spindle", "bearing_wear"): ["Rear bearing outer race wear (lubrication breakdown)",
                                  "Front angular contact bearing wear (coolant ingress)"],
    ("spindle", "misalignment"): ["Drive belt and pulley misalignment after belt change"],
    ("spindle", "overheating"): ["Spindle cooling flow loss (clogged coolant filter)"],
    ("press", "bearing_wear"): ["Hydraulic pump motor bearing wear"],
    ("press", "misalignment"): ["Pump-motor coupling misalignment"],
    ("press", "overheating"): ["Hydraulic oil overheating (oil cooler fouled)"],
    ("robot", "bearing_wear"): ["Axis 2 reducer bearing wear"],
    ("robot", "misalignment"): ["Axis 3 gearbox mounting bolts loose (misalignment)"],
    ("robot", "overheating"): ["Axis motor overheating (cabinet fan failed)"],
    ("inspection", "bearing_wear"): ["Torque sensor drive bearing wear"],
    ("inspection", "misalignment"): ["Coupling misalignment between motor and torque sensor"],
    ("inspection", "overheating"): ["Drive motor overheating (cooling fan failed)"],
    ("conveyor", "bearing_wear"): ["Drive drum bearing wear"],
    ("conveyor", "misalignment"): ["Belt tracking and drum misalignment"],
    ("conveyor", "overheating"): ["Drive motor overload (jammed return rollers)"],
}
PARTS = {
    ("spindle", "bearing_wear"): [[("SKF-6205", 1)], [("SKF-7008", 2)]],
    ("spindle", "misalignment"): [[("BLT-HTD-8M", 1)], []],
    ("spindle", "overheating"): [[("FLT-SPC-10", 1)]],
    ("press", "bearing_wear"): [[("SKF-6308", 2)]],
    ("press", "misalignment"): [[("CPL-42", 1)], []],
    ("press", "overheating"): [[("FLT-HYD-10", 1), ("OIL-HLP46-20L", 2)]],
    ("robot", "bearing_wear"): [[("HDG-32-BRG", 1)]],
    ("robot", "misalignment"): [[]],
    ("robot", "overheating"): [[("FAN-RB6", 1)]],
    ("inspection", "bearing_wear"): [[("SKF-6202", 2)]],
    ("inspection", "misalignment"): [[("CPL-19", 1)], []],
    ("inspection", "overheating"): [[("FAN-TT2", 1)]],
    ("conveyor", "bearing_wear"): [[("SKF-6204", 2)]],
    ("conveyor", "misalignment"): [[("BLT-CNV-600", 1)], []],
    ("conveyor", "overheating"): [[("MTR-CNV-075", 1)], []],
}
MODE_WORD = {"bearing_wear": "bearing failure", "misalignment": "misalignment", "overheating": "overheating"}


def _tech(a: Asset, mode: str | None, rng: np.random.Generator) -> str | None:
    if a.plant_id != "PLANT-A":
        return None
    if a.type == "press" and mode != "bearing_wear":
        return TECH["hyd"][0]
    key = {"bearing_wear": "bearing", "misalignment": "align", "overheating": "elec"}.get(mode or "", "bearing")
    if a.type in ("robot", "inspection") and mode != "misalignment":
        key = "elec"
    return str(rng.choice(TECH[key]))


def _findings(a: Asset, mode: str, prm: dict, D: float, rep_h: float, rng) -> str:
    if mode == "bearing_wear":
        if prm:
            pct = round(100 * (prm["R40"] - 1 + 0.1 + 0.9 * prm["S"]) / 10) * 10
            kurt = a.base["vibration_kurtosis"] + prm["K1"] + prm["K2"]
        else:
            pct, kurt = int(rng.integers(8, 16)) * 10, float(rng.uniform(4.6, 6.2))
        dmg = rng.choice(["outer race spalling", "inner race pitting", "cage wear and discoloured grease",
                          "outer race worn, grease dry"])
        return (f"Vibration rose ~{pct}% over {max(1, round(D / 24))} days before the stop; kurtosis peaked at "
                f"{kurt:.1f}. Found {dmg}. Bearing replaced, run-in at 50% speed for 30 min, new baseline recorded. "
                f"Machine down {rep_h:.1f} h.")
    if mode == "misalignment":
        pct = round(100 * ((prm.get("A", rng.uniform(1, 1.8))) + 0.5 * prm.get("S", 0.5)) / 10) * 10
        off = rng.uniform(0.09, 0.25)
        return (f"Vibration rose ~{pct}% over {max(1, round(D / 24))} days with normal kurtosis (~3), strongest at "
                f"2x running speed. Measured offset {off:.2f} mm (limit 0.05 mm). Re-aligned with the laser tool, "
                f"checked mounting bolts. Machine down {rep_h:.1f} h.")
    rise = prm.get("H", rng.uniform(16, 28)) + 4
    cause = rng.choice(["cooling fan not turning", "filter blocked", "cooling fins packed with dust and chips",
                        "coolant flow below 60% of nominal"])
    return (f"Bearing and motor temperature climbed ~{rise:.0f} °C over {max(2, round(D))} h; vibration almost "
            f"unchanged. Cause: {cause}. Cleaned and replaced parts, temperatures back to normal after 1 h. "
            f"Machine down {rep_h:.1f} h.")


def _corrective(a: Asset, mode: str, created: pd.Timestamp, start: pd.Timestamp, rep_h: float, prm: dict,
                D: float, rng) -> dict:
    parts_opts = PARTS[(a.type, mode)]
    parts = parts_opts[int(rng.integers(len(parts_opts)))]
    roots = ROOT[(a.type, mode)]
    return dict(asset_id=a.id, type="corrective", title=f"{a.name} {a.id} {MODE_WORD[mode]}",
                root_cause=str(roots[int(rng.integers(len(roots)))]), findings=_findings(a, mode, prm, D, rep_h, rng),
                parts=[{"sku": s, "quantity": q} for s, q in parts], technician_id=_tech(a, mode, rng),
                planned_start=start, duration_hours=round(rep_h * 2) / 2 or 0.5, status="done",
                created_at=created)


def _preventive(a: Asset, start: pd.Timestamp, hours: float, rng) -> dict:
    what = {"spindle": "Lubrication, belt tension and bearing check",
            "press": "Hydraulic oil and filter change, pump coupling check",
            "robot": "Reducer grease change and cable check",
            "inspection": "Calibration check and coupling inspection",
            "conveyor": "Belt tracking, drum bearings and drive check"}[a.type]
    note = rng.choice(["OK", "OK, all values within limits", "OK; vibration and temperature normal",
                       "Minor wear noted, no action needed"])
    parts = [{"sku": "FLT-HYD-10", "quantity": 1}] if a.type == "press" else []
    return dict(asset_id=a.id, type="preventive", title=f"{a.name} {a.id}: {what.lower()}", root_cause=None,
                findings=note, parts=parts, technician_id=_tech(a, None, rng), planned_start=start,
                duration_hours=round(hours * 2) / 2 or 0.5, status="done",
                created_at=(start - pd.Timedelta(days=3)).normalize() + pd.Timedelta(hours=9))


def _number(year: int, ts: pd.Timestamp) -> float:
    pts = [(pd.Timestamp(t).value, n) for t, n in ANCHORS[year]]
    return float(np.interp(ts.value, [p[0] for p in pts], [p[1] for p in pts]))


def _assign_ids(items: list[dict]) -> None:
    """Give every work order without an id a WO-<year>-<n> that fits the plant-wide counter and the fixed ids."""
    by_year: dict[int, list[dict]] = {}
    for w in items:
        by_year.setdefault(w["created_at"].year, []).append(w)
    for year, ws in by_year.items():
        ws.sort(key=lambda w: w["created_at"])
        cap = ANCHORS[year][-1][1] - 1                     # highest number allowed this year
        lo, seg = 0, []
        for w in ws + [None]:                              # None closes the last segment
            if w is not None and not w.get("id"):
                seg.append(w)
                continue
            hi = int(w["id"].split("-")[2]) if w is not None else cap + 1
            nums, prev = [], lo
            for x in seg:                                  # forward: follow the counter, keep increasing
                prev = max(int(round(_number(year, x["created_at"]))), prev + 1)
                nums.append(prev)
            nxt = hi
            for i in range(len(nums) - 1, -1, -1):         # backward: stay below the next fixed number
                nums[i] = min(nums[i], nxt - 1)
                nxt = nums[i]
            if nums and nums[0] <= lo:
                raise ValueError(f"too many work orders in {year} before number {hi}")
            for x, n in zip(seg, nums):
                x["id"] = f"WO-{year}-{n:04d}"
            if w is not None:
                lo, seg = hi, []


def build(assets: list[Asset], tl: Timeline) -> tuple[list[dict], dict]:
    rng = np.random.default_rng([SEED, 5150])
    where = {a.id: a for a in assets}
    items: list[dict] = []

    # 1) the two EQ-3012 work orders from the story fixture, unchanged
    for w in json.loads((FIXTURES / "history_EQ-3012.json").read_text(encoding="utf-8")):
        w = dict(w)
        w["created_at"] = pd.Timestamp(w["created_at"])
        w["planned_start"] = pd.Timestamp(w["planned_start"]) if w.get("planned_start") else None
        w["_fixed"] = True
        items.append(w)

    # 2) every breakdown and service inside the 90 days
    for f in tl.failures:
        if f.story:
            continue
        a = where[f.asset_id]
        rep_h = (f.repair_end - f.repair_start) / pd.Timedelta(hours=1)
        D = (f.failure_at - f.degradation_start) / pd.Timedelta(hours=1)
        w = _corrective(a, f.mode, f.failure_at + pd.Timedelta(minutes=10), f.repair_start, rep_h, f.params, D, rng)
        w["_ref"] = f.id
        items.append(w)
    for s in tl.services:
        w = _preventive(where[s.asset_id], s.start, (s.end - s.start) / pd.Timedelta(hours=1), rng)
        w["_ref"] = ("svc", s.asset_id, s.start)
        items.append(w)
    # 3) the last service before the data starts (that is where 'hours since service' counts from)
    for aid, t in tl.last_service_before.items():
        if aid == story.ASSET_ID or t - pd.Timedelta(days=4) < HISTORY_FROM:
            continue
        items.append(_preventive(where[aid], t - pd.Timedelta(hours=2), 2.0, rng))

    # 4) older history to reach ~200
    others = [a for a in assets if a.id != story.ASSET_ID]
    first = HISTORY_FROM + pd.Timedelta(days=4)                 # preventive orders are created 3 days ahead
    span = (START - pd.Timedelta(days=2) - first) / pd.Timedelta(hours=1)
    while len(items) < TARGET:
        a = others[int(rng.integers(len(others)))]
        t = (first + pd.Timedelta(hours=float(rng.uniform(0, span)))).floor("10min")
        if t >= tl.last_service_before.get(a.id, START) - pd.Timedelta(days=2):
            continue                                          # keep 'last service' really the last
        kind = rng.choice(["corrective", "preventive", "predictive"], p=[0.5, 0.4, 0.1])
        if kind == "preventive":
            items.append(_preventive(a, t, float(rng.uniform(1, 3)), rng))
            continue
        mode = str(rng.choice(["bearing_wear", "misalignment", "overheating"], p=[0.5, 0.25, 0.25]))
        rep_h = float(rng.uniform(1.5, 9))
        D = float(rng.uniform(30, 170))
        w = _corrective(a, mode, t, t + pd.Timedelta(hours=float(rng.uniform(0.5, 3))), rep_h, {}, D, rng)
        if kind == "predictive":
            w["type"] = "predictive"
            w["title"] = f"{a.name} {a.id}: planned repair, early signs of {MODE_WORD[mode]}"
            w["findings"] = w["findings"].replace("before the stop", "before the planned stop")
        items.append(w)

    _assign_ids(items)
    items.sort(key=lambda w: w["created_at"])
    ids = {}
    out = []
    for w in items:
        if "_ref" in w:
            ids[w["_ref"]] = w["id"]
        clean = {k: v for k, v in w.items() if not k.startswith("_")}
        out.append(WorkOrder.model_validate(clean).model_dump(mode="json"))
    return out, ids


def write(wos: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
