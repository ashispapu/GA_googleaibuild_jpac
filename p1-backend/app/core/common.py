"""Small shared helpers: clock and thread-safe id sequences."""
import threading
from datetime import datetime, timezone
from typing import Callable

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime; the default clock for every service."""
    return datetime.now(timezone.utc)


class IdSequence:
    """Human-readable ids such as INC-0042 or STO-4500012. Starts at the golden-story numbers (CONTRACTS C9)."""

    def __init__(self, prefix: str, start: int, width: int = 0):
        """prefix: text before the number; start: first number; width: zero-pad width (0 = no padding)."""
        self.prefix, self.start, self.width = prefix, start, width
        self._n, self._lock = start, threading.Lock()

    def next(self) -> str:
        """Return the next id (e.g. INC-0042) and advance the counter. Thread-safe."""
        with self._lock:
            n, self._n = self._n, self._n + 1
        return f"{self.prefix}{str(n).zfill(self.width)}"

    def reset(self) -> None:
        """Go back to the first number; used when the simulator resets the world."""
        with self._lock:
            self._n = self.start
