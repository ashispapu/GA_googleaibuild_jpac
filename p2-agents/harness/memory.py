"""Case memory (AG-9): what technicians confirmed or rejected, per asset + failure mode. In memory by default; the
backend owns the persisted numbers (ConfidenceUpdater). Diagnosis can read it when [agent.diagnosis] policy
use_case_memory = true (off by default so the demo shows the same numbers on every run)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class Case:
    incident_id: str
    asset_id: str
    failure_mode: str | None
    confirmed: bool
    similar_case_confidence: float | None
    actual_root_cause: str | None = None


@dataclass
class CaseMemory:
    cases: list[Case] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, case: Case) -> None:
        with self._lock:
            self.cases.append(case)

    def similar(self, asset_id: str, failure_mode: str | None) -> list[Case]:
        with self._lock:
            return [c for c in self.cases if c.asset_id == asset_id and c.failure_mode == failure_mode]

    def confidence_for(self, asset_id: str, failure_mode: str | None) -> float | None:
        hits = [c.similar_case_confidence for c in self.similar(asset_id, failure_mode)
                if c.confirmed and c.similar_case_confidence is not None]
        return hits[-1] if hits else None
