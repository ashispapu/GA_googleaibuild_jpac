"""Checks that run after every generate. Any failure makes `python -m data_gen.generate` exit with code 1."""
from __future__ import annotations

import json

import pandas as pd
import yaml

from . import story
from .plant import P4, REPO, RULE_MINUTES, RULE_RMS, SIGNALS
from .timeline import END, GRID

RANGES_FALLBACK = {"vibration_rms": (0, 20), "vibration_peak": (0, 50), "vibration_kurtosis": (1, 20),
                   "bearing_temp": (0, 120), "motor_current": (0, 60), "power_kw": (0, 30), "spindle_rpm": (0, 15000)}


def _tag_ranges() -> dict:
    f = REPO / "config" / "tag_map.yaml"
    if not f.exists():
        return RANGES_FALLBACK
    out = dict(RANGES_FALLBACK)
    for tag, v in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).items():
        if isinstance(v, dict) and "signal" in v and "range" in v:
            out[v["signal"]] = tuple(v["range"])
    return out


def run(data: pd.DataFrame, tl, assets, wos: list[dict], fe: pd.DataFrame, only=None) -> list[str]:
    bad: list[str] = []

    def check(ok, msg):
        if not ok:
            bad.append(msg)

    # ---------------- training data ----------------
    if only in (None, "data"):
        check(data["asset_id"].nunique() == len(assets) == 20, f"expected 20 machines, got {data['asset_id'].nunique()}")
        check(len(data) == len(assets) * len(GRID), "one row per machine per minute")
        check(data["ts"].min() == GRID[0] and data["ts"].max() == END, "time range is the 90 days before the alert")
        check(str(data["ts"].dt.tz) == "UTC", "timestamps are UTC")
        check(not data[SIGNALS].isna().any().any(), "no missing signal values")
        for sig, (lo, hi) in _tag_ranges().items():
            if sig in data:
                v = data[sig]
                check(v.min() >= lo and v.max() <= hi, f"{sig} stays inside tag_map range {lo}..{hi}")
        check(set(data["state"].unique()) <= {"run", "idle", "fault", "stopped"}, "machine states are contract values")
        rate = data["failure_within_72h"].mean()
        check(0.02 < rate < 0.15, f"share of 'breaks down within 72 h' rows is sensible ({rate:.1%})")
        real = fe[~fe["story"]]
        check(30 <= len(real) <= 58, f"30-58 planted breakdowns ({len(real)})")
        check(set(real["failure_mode"]) == {"bearing_wear", "misalignment", "overheating"}, "all 3 failure modes")
        for _, f in real.iterrows():
            g = data[(data["asset_id"] == f["asset_id"]) & (data["failure_id"] == f["failure_id"])]
            check(len(g) > 0, f"{f['failure_id']} has labelled rows")
        # the demo spindle at the alert
        s = data[data["asset_id"] == story.ASSET_ID].set_index("ts")
        last10 = s.loc[END - pd.Timedelta(minutes=9):END]
        ago = s.loc[END - pd.Timedelta(hours=6, minutes=9):END - pd.Timedelta(hours=6)]
        rms, rms6 = last10["vibration_rms"].mean(), ago["vibration_rms"].mean()
        check(4.15 <= rms <= 4.45, f"EQ-3012 vibration at the alert is ~4.3 mm/s ({rms:.2f})")
        check(0.28 <= rms / rms6 - 1 <= 0.40, f"EQ-3012 vibration +34% in 6 h ({rms / rms6 - 1:+.0%})")
        k = last10["vibration_kurtosis"].mean()
        check(4.8 <= k <= 5.4, f"EQ-3012 kurtosis ~5.1 ({k:.2f})")
        dt = last10["bearing_temp"].mean() - ago["bearing_temp"].mean()
        check(7.6 <= dt <= 9.6, f"EQ-3012 bearing temp +8.6 degC in 6 h ({dt:+.1f})")
        pw = last10["power_kw"].mean() / story.BASE["power_kw"] - 1
        check(0.06 <= pw <= 0.10, f"EQ-3012 power +8% vs baseline ({pw:+.1%})")
        hss = s["hours_since_service"].iat[-1]
        check(abs(hss - 3150) < 10, f"EQ-3012 ~3,150 h since last service ({hss:.0f})")
        over = (s["vibration_rms"] > RULE_RMS).astype(int)
        longest = over.groupby((over != over.shift()).cumsum()).sum().max()
        check(longest < RULE_MINUTES, "the old 4.5 mm/s rule has NOT fired on EQ-3012 before the alert")
        q = s.loc[:story.DEGRADATION_START, "vibration_rms"].quantile(0.99)
        check(q < 3.0, f"EQ-3012 is normal before its 3-day story (99th percentile {q:.2f} mm/s)")

    # ---------------- work orders ----------------
    if only in (None, "corpus"):
        ids = [w["id"] for w in wos]
        check(190 <= len(wos) <= 210, f"~200 work orders ({len(wos)})")
        check(len(ids) == len(set(ids)), "work order ids are unique")
        check("WO-2026-0153" not in ids, "WO-2026-0153 is left for the demo to create")
        fx = json.loads((REPO / "fixtures" / "story" / "history_EQ-3012.json").read_text(encoding="utf-8"))
        for w in fx:
            got = next((x for x in wos if x["id"] == w["id"]), None)
            check(got == w, f"{w['id']} matches fixtures/story/history_EQ-3012.json exactly")
        check([w["id"] for w in wos if w["asset_id"] == story.ASSET_ID] == [w["id"] for w in fx],
              "EQ-3012 has exactly the fixture's work orders")
        n26 = [int(i.split("-")[2]) for i in ids if i.startswith("WO-2026-")]
        check(max(n26) <= 152, "2026 numbers stay below WO-2026-0153")
        check(all(w["created_at"] <= END.isoformat().replace("+00:00", "Z") for w in wos), "no work order after the alert")
        real = fe[~fe["story"]]
        check((real["work_order_id"] != "").all(), "every breakdown in the data has a corrective work order")
        check_manuals(check)
    return bad


def check_manuals(check) -> None:
    """The search fixture must be literally true: MAN-SPINDLE-200 section 7.4 on page 58, 7.6 on page 61."""
    import re
    from pypdf import PdfReader
    from .manual_text import FIXTURE_7_4, FIXTURE_7_6
    man = P4 / "corpus" / "manuals"
    pdfs = sorted(man.glob("*.pdf"))
    check(3 <= len(pdfs) <= 5, f"3-5 manuals in corpus/manuals ({len(pdfs)})")
    check(not any("hidden" in p.name.lower() or "poison" in p.name.lower() for p in pdfs),
          "the poisoned copy is NOT in corpus/manuals")
    norm = lambda t: re.sub(r"\s+", " ", t or "")
    spindle = next((p for p in pdfs if p.name.startswith("MAN-SPINDLE-200")), None)
    check(spindle is not None, "MAN-SPINDLE-200 is in corpus/manuals")
    if spindle:
        r = PdfReader(str(spindle))
        check(FIXTURE_7_4 in norm(r.pages[57].extract_text()), "section 7.4 text is on page 58")
        check(FIXTURE_7_6 in norm(r.pages[60].extract_text()), "section 7.6 text is on page 61")
    fx = json.loads((REPO / "fixtures" / "story" / "docsearch_bearing.json").read_text(encoding="utf-8"))
    for c in fx["chunks"]:
        if c["doc_id"] == "MAN-SPINDLE-200" and spindle:
            check(norm(c["text"]) in norm(r.pages[c["page"] - 1].extract_text()),
                  f"docsearch fixture chunk {c['section']} matches page {c['page']} word for word")
    idx = json.loads((man / "index.json").read_text(encoding="utf-8"))
    check({d["doc_id"] for d in idx} == {p.name.split("_")[0] for p in pdfs}, "index.json lists every manual")
    check((REPO / "fixtures" / "security").exists() and any((REPO / "fixtures" / "security").glob("*.pdf")),
          "the security-demo copy is in fixtures/security")
