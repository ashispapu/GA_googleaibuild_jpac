"""Time maths shared by the planning agents: shift windows, tariff-weighted energy cost, overlap with schedule slots.
All datetimes are timezone-aware UTC (C0). Pure functions, no I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

UTC = timezone.utc


def hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def parse_shift(spec: str) -> tuple[int, int]:
    """'22-6' -> (22, 6)"""
    a, b = spec.split("-")
    return int(a) % 24, int(b) % 24


def next_shift_start(after: datetime, start_hour: int) -> datetime:
    """First time >= `after` where the clock reads start_hour:00 (UTC)."""
    t = after.astimezone(UTC).replace(minute=0, second=0, microsecond=0, hour=start_hour)
    while t < after:
        t += timedelta(days=1)
    return t


def shift_of(t: datetime, shifts: dict[str, tuple[int, int]]) -> str | None:
    h = t.astimezone(UTC).hour
    for name, (a, b) in shifts.items():
        if (a < b and a <= h < b) or (a > b and (h >= a or h < b)) or a == b:
            return name
    return None


def tariff_price(t: datetime, tariffs: Sequence[dict]) -> float:
    """€/kWh at time t. tariffs: [{"start_hour", "end_hour", "price_per_kwh": {"amount"}}] (wrapping allowed)."""
    h = t.astimezone(UTC).hour
    for tf in tariffs:
        a, b = int(tf["start_hour"]), int(tf["end_hour"])
        if (a < b and a <= h < b) or (a > b and (h >= a or h < b)) or a == b:
            return float(tf["price_per_kwh"]["amount"])
    return float(tariffs[0]["price_per_kwh"]["amount"]) if tariffs else 0.0


def energy_cost(kw: float, start: datetime, end: datetime, tariffs: Sequence[dict], step_min: int = 5) -> float:
    """Cost of drawing `kw` from start to end, priced per tariff band (integrated in `step_min` steps)."""
    if end <= start or not kw:
        return 0.0
    total, t, step = 0.0, start, timedelta(minutes=step_min)
    while t < end:
        nxt = min(t + step, end)
        total += kw * hours(t, nxt) * tariff_price(t, tariffs)
        t = nxt
    return total


def overlapping_slot_hours(start: datetime, end: datetime, slots: Iterable[dict], kinds=("production",)) -> float:
    """Total length of the schedule slots touched by [start, end) - an unplanned stop loses whole shifts."""
    total = 0.0
    for s in slots:
        if s.get("kind", "production") not in kinds:
            continue
        a, b = _dt(s["start"]), _dt(s["end"])
        if a < end and b > start:
            total += hours(a, b)
    return total


def _dt(v) -> datetime:
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def to_dt(v) -> datetime:
    return _dt(v)


def round_to(x: float, step: float) -> float:
    return round(x / step) * step if step else x
