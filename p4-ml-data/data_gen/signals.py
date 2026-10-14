"""One-minute sensor readings for one machine, built from the timeline."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from . import story
from .plant import Asset
from .timeline import END, GRID, N, SEED, START, Service, Timeline, weekly_stop_mask

RUN, IDLE, FAULT, STOPPED = 0, 1, 2, 3
STATE_NAMES = np.array(["run", "idle", "fault", "stopped"])
LAG_MIN = 40.0                     # bearing temperature follows the load with a ~40 min delay
SMALL = ("robot", "inspection", "conveyor")


def _idx(t: pd.Timestamp) -> int:
    """Position of time t on the 1-minute grid (clipped)."""
    return int(np.clip((t - START) // pd.Timedelta(minutes=1) - 1, -1, N))


def _slice(t0: pd.Timestamp, t1: pd.Timestamp) -> slice:
    return slice(max(_idx(t0) + 1, 0), max(min(_idx(t1) + 1, N), 0))


def _ar1(rng: np.random.Generator, n: int, phi: float, sd: float) -> np.ndarray:
    e = rng.normal(0, sd * math.sqrt(1 - phi ** 2), n)
    return lfilter([1.0], [1.0, -phi], e)


def _lag(x: np.ndarray, tau: float) -> np.ndarray:
    alpha = 1 - math.exp(-1.0 / tau)
    y, _ = lfilter([alpha], [1, -(1 - alpha)], x, zi=[x[0] * (1 - alpha)])
    return y


def degradation(a: Asset, tl: Timeline) -> dict[str, np.ndarray]:
    """Multiplicative (rms, power) and additive (kurt, temp) effects of breakdowns and harmless episodes."""
    eff = {"rms": np.ones(N), "power": np.ones(N), "kurt": np.zeros(N), "temp": np.zeros(N)}
    tsc = 0.7 if a.type in SMALL else 1.0
    th = ((GRID - START) / pd.Timedelta(hours=1)).to_numpy()          # hours since START, per minute
    for f in tl.failures_of(a.id):
        if f.story:
            continue
        sl = _slice(f.degradation_start, f.failure_at)
        if sl.start >= sl.stop:
            continue
        fh = (f.failure_at - START) / pd.Timedelta(hours=1)
        D = (f.failure_at - f.degradation_start) / pd.Timedelta(hours=1)
        tau = fh - th[sl]                                               # hours to failure
        p = np.clip(1 - tau / D, 0, 1)
        P = f.params
        if f.mode == "bearing_wear":
            # like the EQ-3012 story: slow rise for days, a fast step over a few hours (T1 -> T1-L hours before
            # failure), then a slow climb that crosses ~R40 x normal about 40 h before failure, then a final surge
            T1, L = P["T1"], P["L"]
            E = T1 - L                                         # end of the fast step (hours before failure)
            u = np.clip((D - tau) / (D - T1), 0, 1)
            v = np.clip((T1 - tau) / L, 0, 1)
            late = np.clip(E - tau, 0, None)
            B = 1 + P["A1"] + P["A2"]
            R40 = max(P["R40"], B + 0.03)
            climb = np.where(tau >= 40, (R40 - B) * late / max(E - 40, 1),
                             (R40 - B) + 0.1 * (40 - tau) / 40 + P["S"] * (np.exp(-tau / P["k"]) - math.exp(-40 / P["k"])))
            rms = 1 + P["A1"] * u ** 1.6 + P["A2"] * v ** 1.6 + np.where(tau < E, climb, 0)
            kurt = P["K1"] * u ** 1.5 + P["K2"] * v
            temp = tsc * (P["Ts"] * u + P["Tf"] * v ** 1.2 + 4 * (late / max(E, 1)) ** 1.5 + 6 * np.exp(-tau / 6))
            power = 1 + P["Pw"] * p ** 1.5 + 0.06 * np.exp(-tau / 10)
        elif f.mode == "misalignment":
            rms = 1 + P["A"] * p ** 1.5 + P["S"] * np.exp(-tau / P["k"])
            kurt = 0.25 * p
            temp = tsc * P["Tm"] * p
            power = 1 + P["Pm"] * p
        else:  # overheating
            temp = tsc * (P["H"] * p ** 1.8 + 4 * np.exp(-tau / 4))
            power = 1 + P["Po"] * p ** 1.2
            rms = 1 + P["R"] * p
            kurt = 0.1 * p
        eff["rms"][sl] *= rms
        eff["power"][sl] *= power
        eff["kurt"][sl] += kurt
        eff["temp"][sl] += temp
    for e in tl.episodes:
        if e.target != a.id:
            continue
        sl = _slice(e.start, e.end)
        n = sl.stop - sl.start
        if n <= 0:
            continue
        x = np.linspace(0, 1, n)
        shape = np.ones(n) if e.kind == "load" else np.sin(np.pi * x) ** 1.5
        if e.kind == "load":                                   # soft edges over 10 min
            ramp = np.minimum(1, np.minimum(np.arange(n), np.arange(n)[::-1]) / 10)
            shape = ramp
        P = e.params
        eff["rms"][sl] *= 1 + (P["rms"] - 1) * shape
        eff["power"][sl] *= 1 + (P["power"] - 1) * shape
        eff["kurt"][sl] += P["kurt"] * shape
        eff["temp"][sl] += P["temp"] * shape
    return eff


def states_and_services(a: Asset, tl: Timeline, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Machine state per minute and run-hours since last maintenance; adds preventive services to the timeline."""
    st = np.full(N, RUN, dtype=np.int8)
    st[weekly_stop_mask(GRID)] = IDLE
    resets = []
    for f in tl.failures_of(a.id):
        if f.story:
            continue
        st[_slice(f.failure_at, f.repair_start)] = FAULT
        st[_slice(f.repair_start, f.repair_end)] = STOPPED
        resets.append(_idx(f.repair_end))
    busy = [(_idx(f.degradation_start) - 360, _idx(f.repair_end)) for f in tl.failures_of(a.id) if not f.story]

    def hours_since(reset_points):
        run = (st == RUN).astype(np.float64) / 60.0
        h = np.empty(N)
        acc = tl.hss_start[a.id]
        last = 0
        for r in sorted(p for p in reset_points if 0 <= p < N) + [N]:
            seg = np.cumsum(run[last:r]) + acc
            h[last:r] = seg
            acc = 0.0
            last = r
        return h

    for _ in range(4):                                       # at most a few services in 90 days
        hss = hours_since(resets)
        due = np.flatnonzero(hss >= tl.service_interval[a.id])
        if not len(due):
            break
        i = int(due[0])
        # next weekday 08:00 UTC after it falls due, outside any breakdown
        t = (GRID[i] + pd.Timedelta(hours=16)).normalize() + pd.Timedelta(hours=8)
        while t.dayofweek == 6:
            t += pd.Timedelta(days=1)
        j = _idx(t)
        if j >= N - 60:
            break
        if any(b0 <= j <= b1 for b0, b1 in busy):
            resets.append(j)                                 # a repair is already happening; it counts as service
            continue
        dur = float(rng.uniform(1.0, 3.0))
        end = t + pd.Timedelta(hours=dur)
        st[_slice(t, end)] = STOPPED
        tl.services.append(Service(a.id, t, end))
        resets.append(_idx(end))
    return st, hours_since(resets)


def build_asset(a: Asset, tl: Timeline) -> pd.DataFrame:
    rng = np.random.default_rng([SEED, 7, a.num])
    st, hss = states_and_services(a, tl, rng)
    eff = degradation(a, tl)
    b = a.base

    # line speed (1.0 = 100 %)
    speed = np.ones(N)
    for e in tl.episodes:
        if e.kind == "speed" and e.target == f"{a.plant_id}/{a.line_id}":
            speed[_slice(e.start, e.end)] = e.params["speed"]
    # grease warm-up after a service (+5-9 degC, fades over ~2 h)
    for s in tl.services:
        if s.asset_id == a.id:
            sl = _slice(s.end, s.end + pd.Timedelta(hours=8))
            k = np.arange(sl.stop - sl.start)
            eff["temp"][sl] += rng.uniform(5, 9) * np.exp(-k / 120)

    lv = _ar1(rng, N, 0.998, 0.025) * (0.1 if a.id == story.ASSET_ID else 1.0)
    # each machine's "normal" drifts a little over weeks and shifts after every repair (new bearing, new belt...)
    drift = {k: _ar1(rng, N, 0.99999, sd) for k, sd in (("rms", 0.04), ("temp", 1.0), ("power", 0.025))}
    for f in tl.failures_of(a.id):
        if not f.story:
            j = max(_idx(f.repair_end) + 1, 0)
            if j < N:
                drift["rms"][j:] += rng.normal(0, 0.05)
                drift["temp"][j:] += rng.normal(0, 1.2)
                drift["power"][j:] += rng.normal(0, 0.02)
    if a.id == story.ASSET_ID:
        drift = {k: v * 0.0 for k, v in drift.items()}               # the demo spindle stays exactly on its story
    run = st == RUN
    local_h = (GRID.hour + GRID.minute / 60 + 5.5) % 24                # IST
    amb = 29 + 2.0 * np.sin(2 * np.pi * (np.asarray(local_h) - 9) / 24)

    rms_mean = b["vibration_rms"] * (1 + drift["rms"]) * speed ** 1.2 * (1 + 0.5 * lv) * eff["rms"]
    kurt_mean = b["vibration_kurtosis"] + eff["kurt"]
    pw_mean = b["power_kw"] * (1 + drift["power"]) * (0.25 + 0.75 * speed) * (1 + lv) * eff["power"]
    temp_run = b["bearing_temp"] + (0.0 if a.id == story.ASSET_ID else 0.5) * (amb - 29) \
        - 8 * (1 - speed) + 4 * lv + eff["temp"] + drift["temp"]

    if a.id == story.ASSET_ID:                                         # the demo story, exactly as in story.py
        sl = _slice(pd.Timestamp(story.DEGRADATION_START) - pd.Timedelta(minutes=1), END + pd.Timedelta(minutes=1))
        lv_s = lv[sl]
        m = [story.means(t.to_pydatetime()) for t in GRID[sl]]
        rms_mean[sl] = np.array([x["vibration_rms"] for x in m]) * (1 + 0.5 * lv_s)
        kurt_mean[sl] = np.array([x["vibration_kurtosis"] for x in m])
        pw_mean[sl] = np.array([x["power_kw"] for x in m]) * (1 + lv_s)
        tm = np.array([x["bearing_temp"] for x in m])
        temp_run[sl] = tm + LAG_MIN * np.gradient(tm) + 4 * lv_s       # pre-compensate the sensor lag

    rms = np.where(run, rms_mean * (1 + rng.normal(0, 0.025, N)), np.abs(0.12 + rng.normal(0, 0.02, N)))
    rms = np.where(st >= FAULT, np.abs(0.06 + rng.normal(0, 0.01, N)), rms)
    kurt = np.where(run, kurt_mean + rng.normal(0, 0.08, N), 3.0 + np.abs(rng.normal(0, 0.3, N)))
    peak = rms * np.where(run, b["crest"] * (1 + 0.06 * (kurt - 3)), 3.0) * (1 + rng.normal(0, 0.03, N))
    power = np.where(run, pw_mean * (1 + rng.normal(0, 0.008, N)),
                     np.where(st == IDLE, 0.08, 0.02) * b["power_kw"] * (1 + rng.normal(0, 0.03, N)))
    current = b["motor_current"] * power / b["power_kw"] * (1 + rng.normal(0, 0.005, N))
    rpm = np.where(run, b["spindle_rpm"] * speed * (1 + rng.normal(0, 0.0012, N)), 0.0)
    temp = _lag(np.where(run, temp_run, amb + 3), LAG_MIN) + rng.normal(0, 0.15, N)

    return pd.DataFrame({
        "ts": GRID, "plant_id": a.plant_id, "line_id": a.line_id, "station_id": a.station_id,
        "asset_id": a.id, "asset_type": a.type, "state": STATE_NAMES[st], "speed_pct": np.round(speed * 100, 1),
        "vibration_rms": np.round(rms, 3).astype("float32"), "vibration_peak": np.round(peak, 3).astype("float32"),
        "vibration_kurtosis": np.round(kurt, 3).astype("float32"), "bearing_temp": np.round(temp, 2).astype("float32"),
        "motor_current": np.round(current, 2).astype("float32"), "power_kw": np.round(power, 3).astype("float32"),
        "spindle_rpm": np.round(rpm, 0).astype("float32"), "hours_since_service": np.round(hss, 2).astype("float32"),
    })


def labels(df: pd.DataFrame, a: Asset, tl: Timeline) -> pd.DataFrame:
    """'Does this machine break down within the next 72 h?' plus hours to breakdown and its type."""
    t = df["ts"].dt.tz_localize(None).to_numpy()
    htf = np.full(N, np.nan)
    mode = np.full(N, "", dtype=object)
    fid = np.full(N, "", dtype=object)
    in_story = np.zeros(N, dtype=bool)
    for f in sorted(tl.failures_of(a.id), key=lambda f: f.failure_at, reverse=True):
        before = t < np.datetime64(f.failure_at.tz_convert(None))
        h = (np.datetime64(f.failure_at.tz_convert(None)) - t) / np.timedelta64(1, "h")
        sel = before & (np.isnan(htf) | (h < htf))
        htf[sel] = h[sel]
        mode[sel] = f.mode
        fid[sel] = f.id
        if f.story:
            in_story |= t >= np.datetime64(f.degradation_start.tz_convert(None))
    within = htf <= 72
    df["failure_within_72h"] = within.astype("int8")
    df["hours_to_failure"] = np.round(htf, 2).astype("float32")
    df["failure_mode"] = np.where(within, mode, "")
    df["failure_id"] = np.where(within, fid, "")
    df["in_story"] = in_story
    return df


def rule_alarm_times(df: pd.DataFrame) -> list[pd.Timestamp]:
    """When the classic rule fires: RMS > 4.5 mm/s for 30 consecutive running minutes."""
    from .plant import RULE_MINUTES, RULE_RMS
    ex = ((df["vibration_rms"].to_numpy() > RULE_RMS) & (df["state"].to_numpy() == "run")).astype(int)
    run_len = np.zeros(len(ex), dtype=int)
    c = 0
    for i, v in enumerate(ex):
        c = c + 1 if v else 0
        run_len[i] = c
    fire = np.flatnonzero(run_len == RULE_MINUTES)
    return [df["ts"].iat[i] for i in fire]
