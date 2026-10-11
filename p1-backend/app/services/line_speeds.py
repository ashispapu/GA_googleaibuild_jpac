"""Current speed of each line. Written by the MES connector (via approved speed_change), read by the telemetry source."""
import threading


class LineSpeeds:
    """Thread-safe map line_id -> speed percent; lines not set run at 100."""

    def __init__(self):
        """Start empty (every line at 100%)."""
        self._speeds: dict[str, int] = {}
        self._lock = threading.Lock()

    def get(self, line_id: str) -> int:
        """Current speed of a line (default 100)."""
        with self._lock:
            return self._speeds.get(line_id, 100)

    def set(self, line_id: str, speed_pct: int) -> None:
        """Store a new speed; called after an approved speed_change."""
        with self._lock:
            self._speeds[line_id] = speed_pct

    def reset(self) -> None:
        """Forget all changes (every line back to 100%)."""
        with self._lock:
            self._speeds.clear()
