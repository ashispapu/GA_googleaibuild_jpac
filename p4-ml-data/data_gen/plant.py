"""The machines, read from the golden plant layout (fixtures/story/plants.json), plus healthy signal levels per type.

The layout is never copied here: add a machine in fixtures/build_fixtures.py, run `make export`,
and the next `python -m data_gen.generate` includes it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import story

P4 = Path(__file__).resolve().parents[1]          # p4-ml-data/
REPO = P4.parent
FIXTURES = REPO / "fixtures" / "story"

SIGNALS = ["vibration_rms", "vibration_peak", "vibration_kurtosis", "bearing_temp",
           "motor_current", "power_kw", "spindle_rpm"]   # the 7 signals in config/tag_map.yaml
RULE_RMS = 4.5            # C5: classic rule = vibration RMS > 4.5 mm/s ...
RULE_MINUTES = 30         # ... sustained for 30 min

# Healthy levels at 100 % line speed. "temp" is the bearing temperature.
TYPE_BASE = {
    #             rms   crest kurt  temp  current power  rpm
    "spindle":    (2.1, 2.76, 3.0, 58.0, 21.0, 11.0, 12000),
    "press":      (2.6, 2.70, 3.2, 52.0, 32.0, 18.5, 1480),
    "robot":      (1.4, 2.90, 3.0, 45.0, 9.0, 4.2, 3000),
    "inspection": (1.1, 2.80, 2.9, 41.0, 6.0, 2.6, 1450),
    "conveyor":   (1.6, 2.75, 3.0, 43.0, 7.5, 3.0, 1450),
}
# Which failure modes each machine type gets, with weights.
MODE_WEIGHTS = {
    "spindle":    {"bearing_wear": 0.60, "misalignment": 0.25, "overheating": 0.15},
    "press":      {"bearing_wear": 0.35, "misalignment": 0.25, "overheating": 0.40},
    "robot":      {"bearing_wear": 0.40, "misalignment": 0.20, "overheating": 0.40},
    "inspection": {"bearing_wear": 0.40, "misalignment": 0.20, "overheating": 0.40},
    "conveyor":   {"bearing_wear": 0.40, "misalignment": 0.40, "overheating": 0.20},
}


@dataclass
class Asset:
    id: str
    name: str
    type: str
    plant_id: str
    line_id: str
    station_id: str
    base: dict = field(default_factory=dict)   # healthy level per signal + "crest"

    @property
    def num(self) -> int:
        return int(self.id.split("-")[1])


def load_assets() -> list[Asset]:
    plants = json.loads((FIXTURES / "plants.json").read_text(encoding="utf-8"))
    out = []
    for p in plants:
        for line in p["lines"]:
            for st in line["stations"]:
                for a in st["assets"]:
                    out.append(Asset(a["id"], a["name"], a["type"], p["id"], line["id"], st["id"]))
    for a in out:
        if a.type not in TYPE_BASE:
            raise ValueError(f"{a.id}: no healthy levels for machine type {a.type!r} (add it to TYPE_BASE)")
        rms, crest, kurt, temp, cur, pw, rpm = TYPE_BASE[a.type]
        if a.id == story.ASSET_ID:            # the demo spindle uses the story numbers exactly
            b = dict(story.BASE)
            b["crest"] = story.CREST
        else:                                  # every machine is a little different
            r = np.random.default_rng([7, a.num])
            k = r.uniform(0.9, 1.1)
            b = {"vibration_rms": rms * r.uniform(0.9, 1.1), "vibration_kurtosis": kurt + r.uniform(-0.1, 0.1),
                 "bearing_temp": temp + r.uniform(-2, 2), "motor_current": cur * k, "power_kw": pw * k,
                 "spindle_rpm": float(rpm), "crest": crest}
            b["vibration_peak"] = b["vibration_rms"] * crest
        a.base = b
    return out


def line_of(assets: list[Asset]) -> dict[tuple[str, str], list[Asset]]:
    lines: dict[tuple[str, str], list[Asset]] = {}
    for a in assets:
        lines.setdefault((a.plant_id, a.line_id), []).append(a)
    return lines
