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


class SharedSimClock(SimClock):
    """The plant clock when several processes run the plant (api x N, engine, worker x N).

    The anchor (plant time at a wall-clock instant) and the speed live in the IT database (meta), so every process
    computes the same plant time; a speed change made through any API instance reaches the others within
    `refresh_s`. Wall time (not a process-local monotonic clock) is used because instances run on different hosts."""

    KEYS = ("clock_anchor_plant", "clock_anchor_wall", "clock_scale")

    def __init__(self, db, start: datetime, scale: float, refresh_s: float = 2.0):
        super().__init__(start, scale, real=time.time)
        self._db, self._refresh_s, self._loaded_at = db, refresh_s, -1e9
        if not self._load():
            self._store()

    def _load(self) -> bool:
        plant, wall, scale = (self._db.meta(k) for k in self.KEYS)
        if not (plant and wall and scale):
            return False
        with self._lock:
            self._anchor_sim, self._anchor_real, self._scale = parse_ts(plant), float(wall), float(scale)
        self._loaded_at = time.monotonic()
        return True

    def _store(self) -> None:
        with self._lock:
            plant, wall, scale = self._anchor_sim, self._anchor_real, self._scale
        self._db.set_meta("clock_anchor_plant", plant.isoformat())
        self._db.set_meta("clock_anchor_wall", repr(wall))
        self._db.set_meta("clock_scale", repr(scale))
        self._loaded_at = time.monotonic()

    def now(self) -> datetime:
        if time.monotonic() - self._loaded_at > self._refresh_s:
            try:
                self._load()
            except Exception:  # database briefly unavailable: keep the last anchor
                self._loaded_at = time.monotonic()
        return super().now()

    def set_scale(self, scale: float) -> None:
        self._load()
        super().set_scale(scale)
        self._store()

    def reanchor(self, plant_time: datetime) -> None:
        """The engine restarted: continue from where the plant stopped (the plant does not age while it is down)."""
        with self._lock:
            self._anchor_sim, self._anchor_real = plant_time, time.time()
        self._store()
