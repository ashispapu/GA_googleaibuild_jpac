"""Plant clock. The simulated plant runs `scale` times faster than real time (60 = one real minute per plant hour),
so shifts, part deliveries and repairs happen while you watch. Everything that belongs to the plant (telemetry,
orders, work orders, incidents, audit) is stamped with plant time; security tokens keep using real time."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone


class SimClock:
    def __init__(self, start: datetime, scale: float, real=time.monotonic):
        self._lock = threading.Lock()
        self._real = real
        self._anchor_sim = start
        self._anchor_real = real()
        self._scale = float(scale)

    def __call__(self) -> datetime:
        return self.now()

    def now(self) -> datetime:
        with self._lock:
            return self._anchor_sim + timedelta(seconds=(self._real() - self._anchor_real) * self._scale)

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, scale: float) -> None:
        """Change speed (0 = paused) without jumping in time."""
        with self._lock:
            now_real = self._real()
            self._anchor_sim = self._anchor_sim + timedelta(seconds=(now_real - self._anchor_real) * self._scale)
            self._anchor_real = now_real
            self._scale = max(0.0, float(scale))

    def real_seconds(self, plant_seconds: float) -> float:
        """How long a plant duration takes in real time (inf when paused)."""
        return plant_seconds / self._scale if self._scale > 0 else float("inf")

    def plant_hours(self, real_seconds: float) -> float:
        return real_seconds * self._scale / 3600


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
