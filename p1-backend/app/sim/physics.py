"""Machine physics: what a machine's sensors read, given its model, its (hidden) condition, line speed and state.
Used for live telemetry, the historian backfill, and the health model's signature matching."""
from __future__ import annotations

import random
from dataclasses import dataclass

SIGNALS = ("vibration_rms", "vibration_peak", "vibration_kurtosis", "bearing_temp", "motor_temp", "spindle_rpm",
           "motor_current", "power_kw")
UNITS = {"vibration_rms": "mm/s", "vibration_peak": "mm/s", "vibration_kurtosis": "", "bearing_temp": "°C",
         "motor_temp": "°C", "spindle_rpm": "rpm", "motor_current": "A", "power_kw": "kW"}
TAG_SUFFIX = {"vibration_rms": "VIB_RMS", "vibration_peak": "VIB_PK", "vibration_kurtosis": "VIB_KURT",
              "bearing_temp": "BRG_T", "motor_temp": "MTR_T", "spindle_rpm": "RPM", "motor_current": "MTR_I",
              "power_kw": "PWR"}
PROTOCOL = {"vibration_rms": "mqtt", "vibration_peak": "mqtt", "vibration_kurtosis": "mqtt", "bearing_temp": "modbus",
            "motor_temp": "modbus", "spindle_rpm": "opcua", "motor_current": "opcua", "power_kw": "opcua"}
RUNNING = "run"
DOWN_STATES = ("stopped", "fault", "idle")


@dataclass(frozen=True)
class Condition:
    """The real (hidden) state of a machine: which fault, how far it has gone (0..1)."""
    mode: str            # bearing_wear | misalignment | overheating
    variant: str         # signature key, e.g. misalignment_loose
    progress: float


def signal_range(baseline: dict, signal: str) -> tuple[float, float]:
    """Plausible instrument range (outside it the OT gateway marks the value out_of_range)."""
    if signal == "vibration_kurtosis":
        return 1.0, 20.0
    if signal in ("vibration_rms", "vibration_peak"):
        return 0.0, round(baseline["vibration_rms"] * (12 if signal == "vibration_peak" else 5), 1)
    if signal in ("bearing_temp", "motor_temp"):
        return 0.0, 150.0
    key = {"spindle_rpm": "spindle_rpm", "motor_current": "motor_current", "power_kw": "power_kw"}[signal]
    return 0.0, round(baseline[key] * 2.5, 1)


def readings(baseline: dict, signatures: dict, cond: Condition | None, speed_pct: float, state: str,
             rng: random.Random | None = None, noise: float = 0.0) -> dict[str, float]:
    """One set of sensor values. `noise` is relative (0.02 = 2 %)."""
    g = (rng or random).gauss
    b = baseline
    if state != RUNNING:
        out = {"vibration_rms": 0.08, "vibration_peak": 0.25, "vibration_kurtosis": 3.0,
               "bearing_temp": b["bearing_temp"] * 0.62, "motor_temp": b["motor_temp"] * 0.6, "spindle_rpm": 0.0,
               "motor_current": 0.4, "power_kw": round(b["power_kw"] * 0.03, 2)}
        if state == "fault" and cond is not None:  # a broken machine: residual heat
            out["bearing_temp"] += 10
            out["motor_temp"] += 8
        return {k: round(v * (1 + g(0, noise / 2)) if noise else v, 3) for k, v in out.items()}
    sf = max(0.0, min(1.0, speed_pct / 100))
    sig = signatures.get(cond.variant) if cond else None
    p = cond.progress if cond else 0.0
    vx = (sig["vibration_rms_x"] - 1) if sig else 0.0
    vib = b["vibration_rms"] * (0.55 + 0.45 * sf) * (1 + vx * p * (0.6 + 0.4 * sf))
    kurt = 3.0 + (sig["kurtosis_add"] * p if sig else 0.0)
    bt = b["bearing_temp"] * (0.85 + 0.15 * sf) + (sig["bearing_temp_add"] * p if sig else 0.0)
    mt = b["motor_temp"] * (0.85 + 0.15 * sf) + (sig["motor_temp_add"] * p if sig else 0.0)
    cur = b["motor_current"] * (0.6 + 0.4 * sf) * (1 + ((sig["current_x"] - 1) * p if sig else 0.0))
    pw = b["power_kw"] * (0.4 + 0.6 * sf) * (1 + ((sig["power_x"] - 1) * p if sig else 0.0))
    rpm = b["spindle_rpm"] * sf
    if noise:
        vib *= 1 + g(0, noise)
        kurt += g(0, 0.05)
        bt += g(0, 0.25)
        mt += g(0, 0.3)
        cur *= 1 + g(0, noise / 2)
        pw *= 1 + g(0, noise / 2)
        rpm *= 1 + g(0, 0.002)
    peak = vib * (3.1 + 0.25 * (kurt - 3.0))
    return {"vibration_rms": round(vib, 3), "vibration_peak": round(peak, 3), "vibration_kurtosis": round(kurt, 3),
            "bearing_temp": round(bt, 2), "motor_temp": round(mt, 2), "spindle_rpm": round(rpm, 1),
            "motor_current": round(cur, 3), "power_kw": round(pw, 3)}


def nominal(baseline: dict, speed_pct: float) -> dict[str, float]:
    """Healthy, noise-free readings at this speed (the reference the health model compares against)."""
    return readings(baseline, {}, None, speed_pct, RUNNING)
