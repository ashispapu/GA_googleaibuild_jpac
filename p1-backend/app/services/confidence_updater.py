"""Feedback learning rule for similar-case confidence (scene 8: 0.60 -> 0.82 when confirmed)."""


class ConfidenceUpdater:
    """Moves a similar-case confidence up when a technician confirms the diagnosis, down when not."""

    def __init__(self, gain: float = 0.55, penalty: float = 0.5):
        """gain: share of the remaining gap closed on confirm; penalty: multiplier on reject."""
        self._gain, self._penalty = gain, penalty

    def updated(self, confidence: float | None, confirmed: bool) -> float | None:
        """New confidence rounded to 2 decimals (0.60 -> 0.82 when confirmed); None stays None."""
        if confidence is None:
            return None
        new = confidence + (1 - confidence) * self._gain if confirmed else confidence * self._penalty
        return round(min(1.0, max(0.0, new)), 2)
