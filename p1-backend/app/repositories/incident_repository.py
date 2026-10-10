"""Incident storage behind a Protocol, so Postgres can replace the in-memory version later."""
import threading
from dataclasses import dataclass, field
from typing import Protocol

from plantguard_contracts.schemas.approvals import Approval, Feedback
from plantguard_contracts.schemas.incidents import AgentEvent, Incident, IncidentStatus, Option, OptionsResponse

ACTIVE = (IncidentStatus.OPEN, IncidentStatus.ANALYSING, IncidentStatus.AWAITING_APPROVAL, IncidentStatus.APPROVED)


@dataclass
class IncidentRecord:
    """Everything stored for one incident: the Incident, its agent events, options, approval and feedback."""

    incident: Incident
    events: list[AgentEvent] = field(default_factory=list)
    options: OptionsResponse | None = None
    approval: Approval | None = None
    feedback: Feedback | None = None
    run_started: bool = False


class IncidentRepository(Protocol):
    """Storage port for incidents; InMemoryIncidentRepository is the current implementation."""

    def next_id(self) -> str:
        """Allocate the next incident id (INC-0042, INC-0043 ...)."""

    def add(self, record: IncidentRecord) -> None:
        """Store a new record."""

    def get(self, incident_id: str) -> IncidentRecord:  # KeyError if unknown
        """Fetch a record by incident id."""

    def find_active(self, asset_id: str) -> IncidentRecord | None:
        """The open/analysing/awaiting/approved incident for an asset, if any."""

    def all(self) -> list[IncidentRecord]:
        """Every stored record."""

    def clear(self) -> None:
        """Delete everything and restart the id sequence (simulator reset)."""

    def approved_option(self, incident_id: str, option_id: str) -> Option | None:
        """The approved Option of an approved/executed incident, else None (used by WriteAuthorizer)."""


class InMemoryIncidentRepository:
    """Thread-safe dict-backed repository. Data is lost on restart."""

    def __init__(self, id_factory, id_reset):
        """id_factory/id_reset: the id sequence's next() and reset()."""
        self._records: dict[str, IncidentRecord] = {}
        self._id_factory, self._id_reset = id_factory, id_reset
        self._lock = threading.RLock()

    def next_id(self) -> str:
        """Allocate the next incident id."""
        return self._id_factory()

    def add(self, record: IncidentRecord) -> None:
        """Store a record under its incident id."""
        with self._lock:
            self._records[record.incident.id] = record

    def get(self, incident_id: str) -> IncidentRecord:
        """Return the record; KeyError (-> 404) if unknown."""
        with self._lock:
            return self._records[incident_id]

    def find_active(self, asset_id: str) -> IncidentRecord | None:
        """First record for the asset whose status is in ACTIVE, else None."""
        with self._lock:
            return next((r for r in self._records.values()
                         if r.incident.asset_id == asset_id and r.incident.status in ACTIVE), None)

    def all(self) -> list[IncidentRecord]:
        """Snapshot list of all records."""
        with self._lock:
            return list(self._records.values())

    def clear(self) -> None:
        """Drop all records and reset the id counter."""
        with self._lock:
            self._records.clear()
            self._id_reset()

    def approved_option(self, incident_id: str, option_id: str) -> Option | None:
        """Used by WriteAuthorizer: only the option that was actually approved can authorize writes."""
        with self._lock:
            rec = self._records.get(incident_id)
            if not rec or not rec.options or rec.incident.selected_option_id != option_id:
                return None
            if rec.incident.status not in (IncidentStatus.APPROVED, IncidentStatus.EXECUTED):
                return None
            return next((o for o in rec.options.options if o.id == option_id), None)
