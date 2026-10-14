"""Everything that happens to the machines in the 90 days: breakdowns, services, weekly stops,
slow-downs and harmless bumps. Signals (signals.py) and work orders (work_orders.py) are both built
from this one timeline, so they always agree."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import story
from .plant import MODE_WEIGHTS, Asset

SEED = int(os.environ.get("PG_SEED", 3))   # 3: held-out numbers closest to the C9 story (see data/README.md)
END = pd.Timestamp(story.ALERT)                         # data ends at the alert (Tue 13 Oct 21:40 UTC)
START = END - pd.Timedelta(days=90)
GRID = pd.date_range(START + pd.Timedelta(minutes=1), END, freq="1min")   # 129,600 minutes
N = len(GRID)
WEEKLY_STOP = (6, 0, 3)          # Sunday 00:00-03:00 UTC: cleaning stop, every machine idle


@dataclass
class Failure:
    id: str
    asset_id: str
    mode: str                    # bearing_wear | misalignment | overheating
    degradation_start: pd.Timestamp
    failure_at: pd.Timestamp
    repair_start: pd.Timestamp
    repair_end: pd.Timestamp
    params: dict
    subtle: bool = False         # weak warning signs (some breakdowns are hard to see coming)
    story: bool = False          # EQ-3012: fails after the data ends; kept out of training


@dataclass
class Service:
    asset_id: str
    start: pd.Timestamp
    end: pd.Timestamp


@dataclass
class Episode:
    kind: str                    # load | bump | grease | speed
    target: str                  # asset id, or "PLANT-A/L3" for line-wide speed changes
    start: pd.Timestamp
    end: pd.Timestamp
    params: dict = field(default_factory=dict)


@dataclass
class Timeline:
    failures: list[Failure]
    services: list[Service]
    episodes: list[Episode]
    hss_start: dict[str, float]          # run hours since last maintenance at START
    service_interval: dict[str, float]
    last_service_before: dict[str, pd.Timestamp]

    def failures_of(self, aid: str) -> list[Failure]:
        return [f for f in self.failures if f.asset_id == aid]


def weekly_stop_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    d, h0, h1 = WEEKLY_STOP
    return np.asarray((idx.dayofweek == d) & (idx.hour >= h0) & (idx.hour < h1))


def run_hours_between(t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """Calendar hours minus the weekly stops (used for 'hours since service' before the data starts)."""
    idx = pd.date_range(t0, t1, freq="1min", inclusive="right")
    return float((~weekly_stop_mask(idx)).sum()) / 60.0


def _params(rng: np.random.Generator, mode: str, subtle: bool) -> tuple[float, dict]:
    u = rng.uniform
    if mode == "bearing_wear":
        D = u(100, 190)
        # 1 in 4 bearings stays on a plateau for longer after the fast step (fails 3-5 days later instead of ~3)
        long = rng.random() < 0.25
        p = dict(T1=u(90, 120) if long else u(66, 80), L=u(5, 12), A1=u(0.35, 0.60), A2=u(0.35, 0.60),
                 R40=u(2.0, 2.35), S=u(0.8, 1.6), k=u(6, 10), K1=u(0.4, 0.8), K2=u(1.0, 2.2), Ts=u(0.6, 1.6),
                 Tf=u(5, 11), Pw=u(0.05, 0.10))
        D = max(D, p["T1"] + 30)
        if subtle:
            for key in ("A1", "A2", "S", "K2", "Tf"):
                p[key] *= 0.45
            p["R40"] = 1 + (p["R40"] - 1) * 0.45
    elif mode == "misalignment":
        D = u(70, 160)
        p = dict(A=u(1.0, 1.8), S=u(0.3, 0.8), k=u(6, 10), Tm=u(3, 6), Pm=u(0.03, 0.06))
        if subtle:
            p["A"] *= 0.45
            p["S"] *= 0.45
    else:  # overheating
        D = u(20, 60)
        p = dict(H=u(16, 28), Po=u(0.08, 0.15), R=u(0.06, 0.14))
        if subtle:
            p["H"] *= 0.45
    return D, p


def build(assets: list[Asset]) -> Timeline:
    failures: list[Failure] = []
    services: list[Service] = []
    episodes: list[Episode] = []
    hss_start, interval, last_before = {}, {}, {}
    fid = 0
    for a in assets:
        rng = np.random.default_rng([SEED, a.num])
        # ---------- service interval and run hours since the last service at START ----------
        if a.id == story.ASSET_ID:
            interval[a.id] = 4400.0                                 # next service is due in December
            last_before[a.id] = pd.Timestamp(story.LAST_SERVICE_END)
            hss_start[a.id] = run_hours_between(last_before[a.id], START)
        else:
            interval[a.id] = float(rng.uniform(3800, 5200))
            hss_start[a.id] = float(rng.uniform(0.05, 0.75) * interval[a.id])
            last_before[a.id] = (START - pd.Timedelta(hours=hss_start[a.id] * 1.013)).floor("h")
        hss_guess = hss_start[a.id]

        # ---------- breakdowns ----------
        n_fail = 0 if a.id == story.ASSET_ID else int(np.clip(rng.poisson(2.8), 1, 4))
        evs: list[tuple] = []
        for _ in range(400):
            if len(evs) == n_fail:
                break
            evs = []
            times = np.sort(rng.uniform(4, 86, n_fail))           # failure day within the 90 days
            ok = True
            prev_end = -1e9
            for t in times:
                mode = str(rng.choice(list(MODE_WEIGHTS[a.type]), p=list(MODE_WEIGHTS[a.type].values())))
                subtle = bool(rng.random() < 0.15)
                D, prm = _params(rng, mode, subtle)
                fail_h = t * 24
                if fail_h - D < prev_end + 48:                       # 2 days of normal running between events
                    ok = False
                    break
                fault_h = rng.uniform(0.5, 2.5)
                rep_h = {"bearing_wear": rng.uniform(2.5, 8), "misalignment": rng.uniform(1.5, 4),
                         "overheating": rng.uniform(1.5, 5)}[mode] * (1.3 if a.type in ("press", "robot") else 1.0)
                evs.append((fail_h, D, mode, subtle, prm, fault_h, rep_h))
                prev_end = fail_h + fault_h + rep_h
            if ok and n_fail:
                # machines long past their last service break down more often: accept this set of breakdown
                # times with a probability that grows with the (approximate) run hours since service
                hss0 = hss_guess
                acc = 1.0
                last_reset = 0.0
                for fail_h, D, mode, *_rest in evs:
                    h = hss0 + (fail_h - last_reset) * 0.98 if last_reset == 0.0 else (fail_h - last_reset) * 0.98
                    acc *= min(1.0, 0.55 + h / 7000)
                    last_reset = fail_h + _rest[-2] + _rest[-1]
                if rng.random() > acc:
                    ok = False
            if not ok:
                evs = []
                n_fail = max(1, n_fail - (1 if rng.random() < 0.05 else 0))
        for fail_h, D, mode, subtle, prm, fault_h, rep_h in evs:
            fid += 1
            f_at = (START + pd.Timedelta(hours=fail_h)).floor("min")
            rs = (f_at + pd.Timedelta(hours=fault_h)).floor("min")
            failures.append(Failure(f"FE-{fid:03d}", a.id, mode, (f_at - pd.Timedelta(hours=D)).floor("min"), f_at, rs,
                                    (rs + pd.Timedelta(hours=rep_h)).floor("min"), prm, subtle))
        if a.id == story.ASSET_ID:
            failures.append(Failure("FE-STORY", a.id, story.FAILURE_MODE, pd.Timestamp(story.DEGRADATION_START),
                                    pd.Timestamp(story.FAILURE), pd.Timestamp(story.FAILURE) + pd.Timedelta(hours=1),
                                    pd.Timestamp(story.FAILURE) + pd.Timedelta(hours=3.5), {}, story=True))


        # ---------- harmless things that look a bit like trouble ----------
        busy = [(f.degradation_start - pd.Timedelta(hours=6), f.repair_end + pd.Timedelta(hours=6)) for f in failures
                if f.asset_id == a.id]
        if a.id == story.ASSET_ID:
            busy.append((END - pd.Timedelta(days=7), END))         # keep the demo spindle's last week clean

        def free(s, e):
            return all(e < b0 or s > b1 for b0, b1 in busy)

        n_bump = 0 if a.id == story.ASSET_ID else rng.poisson(0.6)      # the demo spindle has no scares before its story
        for kind, count, dur in (("load", rng.poisson(9), (2, 10)), ("bump", n_bump, (18, 60))):
            for _ in range(int(count)):
                for _try in range(30):
                    s = START + pd.Timedelta(hours=float(rng.uniform(12, 90 * 24 - 70)))
                    e = s + pd.Timedelta(hours=float(rng.uniform(*dur)))
                    if free(s, e):
                        break
                else:
                    continue
                if kind == "load":         # heavier cut / different product: more shaking and power, bearings fine
                    prm = dict(rms=rng.uniform(1.08, 1.22), power=rng.uniform(1.02, 1.06), temp=rng.uniform(0.5, 1.5),
                               kurt=rng.uniform(0, 0.15))
                else:                      # a scare that clears by itself (e.g. contamination washed out)
                    prm = dict(rms=rng.uniform(1.15, 1.4), kurt=rng.uniform(0.2, 0.8), temp=rng.uniform(1, 3),
                               power=rng.uniform(1.02, 1.05))
                episodes.append(Episode(kind, a.id, s.floor("min"), e.floor("min"), prm))

    # line slow-downs (whole line runs at 70-90 %)
    lines = sorted({(a.plant_id, a.line_id) for a in assets})
    for plant_id, line_id in lines:
        rng = np.random.default_rng([SEED, 900, int(line_id[1:]), ord(plant_id[-1])])
        for _ in range(int(rng.poisson(9))):
            s = START + pd.Timedelta(hours=float(rng.uniform(6, 90 * 24 - 24)))
            e = s + pd.Timedelta(hours=float(rng.uniform(3, 16)))
            if line_id == "L3" and plant_id == "PLANT-A" and e > END - pd.Timedelta(days=5):
                continue                                          # the demo line runs at 100 % before the alert
            episodes.append(Episode("speed", f"{plant_id}/{line_id}", s.floor("min"), e.floor("min"),
                                    dict(speed=float(rng.uniform(0.7, 0.9)))))
    return Timeline(failures, services, episodes, hss_start, interval, last_before)
