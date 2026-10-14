"""Store for RedactionReports: P2's redactor calls add() on every LLM call; GET /security/redactions lists them."""
import threading
from collections import deque

from plantguard_contracts.schemas.security import RedactionReport


class RedactionLog:
    """Keeps the latest redaction reports (max 500) for the Security panel."""

    def __init__(self, max_items: int = 500):
        """max_items: how many reports to keep; older ones drop off."""
        self._items: deque[RedactionReport] = deque(maxlen=max_items)
        self._lock = threading.Lock()

    def add(self, report: RedactionReport) -> None:
        """Store one RedactionReport (called after each LLM call)."""
        with self._lock:
            self._items.append(report)

    def list(self) -> list[RedactionReport]:
        """All stored reports, newest first."""
        with self._lock:
            return list(reversed(self._items))
