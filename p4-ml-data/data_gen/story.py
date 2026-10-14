"""The EQ-3012 demo story (C9), written once.

Used by fixtures/build_fixtures.py (the chart data the API serves) and by data_gen/generate.py
(the training data), so the chart, the training data and the health fixture all tell the same story:

  * normal (2.1 mm/s) until 3 days before the alert,
  * a slow rise to 3.2 mm/s six hours before the alert,
  * then 3.2 -> 4.3 mm/s in the last 6 h (+34 %, kurtosis 5.1, bearing +8.6 degC, power +8 %),
  * alert Tue 2026-10-13 21:40 UTC, 4.3 < 4.5 mm/s so the old rule has NOT fired,
  * the rule would fire about 31 h later, the bearing would fail about 70 h later.

Pure Python (no numpy) so build_fixtures.py stays light.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
ASSET_ID = "EQ-3012"
ALERT = datetime(2026, 10, 13, 21, 40, tzinfo=UTC)      # = build_fixtures.NOW
DEGRADATION_START = ALERT - timedelta(hours=72)
FAILURE = ALERT + timedelta(hours=70)                     # RUL 70 h
LAST_SERVICE_END = datetime(2026, 6, 2, 7, 0, tzinfo=UTC)  # WO-2026-0091 (06:00 + 1 h)
FAILURE_MODE = "bearing_wear"

# Healthy levels at 100 % speed (same numbers as the fixtures and the mock's live stream).
BASE = {
    "vibration_rms": 2.1,        # mm/s
    "vibration_peak": 5.8,       # mm/s
    "vibration_kurtosis": 3.0,
    "bearing_temp": 58.0,        # degC
    "motor_current": 21.0,       # A
    "power_kw": 11.0,            # kW (energy fixture baseline_power_kw)
    "spindle_rpm": 12000.0,      # rpm
}
CREST = BASE["vibration_peak"] / BASE["vibration_rms"]


def means(t: datetime) -> dict[str, float]:
    """Noise-free signal levels of EQ-3012 at time t (any time; healthy before the story)."""
    h = (t - ALERT).total_seconds() / 3600          # hours from the alert, negative = before
    rms, kurt, temp, pw = BASE["vibration_rms"], BASE["vibration_kurtosis"], BASE["bearing_temp"], 1.0
    if h >= -72:
        if h < -6:                                   # slow phase, 3 days
            u = (h + 72) / 66
            rms = 2.1 + 1.1 * u ** 1.6
            kurt = 3.0 + 0.6 * u ** 1.5
            temp = 58.0 + 1.0 * u
        elif h <= 0:                                 # last 6 h before the alert
            v = (h + 6) / 6
            rms = 3.2 + 1.1 * v ** 1.6
            kurt = 3.6 + 1.5 * v
            temp = 59.0 + 8.6 * v ** 1.2
        else:                                        # after the alert (only the live simulator needs this)
            x = min(h, 70.0)
            rms = 4.3 + 0.19 * x / 31 + 1.2 * (math.exp(-(70 - x) / 8) - math.exp(-70 / 8))
            kurt = 5.1 + 0.3 * x / 70
            temp = 67.6 + 4.0 * (x / 70) ** 1.5
        w = min(max((h + 72) / 72, 0.0), 1.0)
        pw = 1 + 0.08 * w ** 1.5 + (0.07 * min(max(h, 0.0), 70.0) / 70 if h > 0 else 0.0)
    power = BASE["power_kw"] * pw
    return {
        "vibration_rms": rms,
        "vibration_peak": rms * CREST * (1 + 0.06 * (kurt - 3.0)),
        "vibration_kurtosis": kurt,
        "bearing_temp": temp,
        "motor_current": BASE["motor_current"] * pw,
        "power_kw": power,
        "spindle_rpm": BASE["spindle_rpm"],
    }


# Noise (1 sigma) used for the 5-minute chart points in the fixtures.
NOISE = {"vibration_rms": 0.05, "vibration_peak": 0.2, "vibration_kurtosis": 0.08, "bearing_temp": 0.25,
         "motor_current": 0.1, "power_kw": 0.05, "spindle_rpm": 15.0}
