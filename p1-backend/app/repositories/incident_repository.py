"""Incident storage behind a Protocol. InMemoryIncidentRepository (fixtures mode, tests) and SqlIncidentRepository
(simulated plant: incidents, every agent event, options per round, approvals and feedback persisted in it.db).
Callers mutate the IncidentRecord they got and then call save(); events are appended with append_event()."""
import json
import threading
from dataclasses import dataclass, field
from typing import Protocol

from plantguard_contracts.schemas.approvals import Approval, Feedback
from plantguard_contracts.schemas.incidents import AgentEvent, Incident, IncidentStatus, Option, OptionsResponse

# v1 (fixtures): an incident stops blocking its machine once its actions were executed.
ACTIVE = (IncidentStatus.OPEN, IncidentStatus.ANALYSING, IncidentStatus.AWAITING_APPROVAL, IncidentStatus.APPROVED,
          IncidentStatus.AWAITING_SIGNOFF)
# simulated plant: it stays open until the technician closes it.
ACTIVE_UNTIL_CLOSED = ACTIVE + (IncidentStatus.EXECUTED, IncidentStatus.IN_REPAIR, IncidentStatus.VERIFYING,
                                IncidentStatus.REPAIR_FAILED)
WRITE_OK = (IncidentStatus.APPROVED, IncidentStatus.EXECUTED)


@dataclass
class IncidentRecord:
    """Everything stored for one incident: the Incident, its agent events, options, approval and feedback."""

    incident: Incident
    events: list[AgentEvent] = field(default_factory=list)
    options: OptionsResponse | None = None
    approval: Approval | None = None
    feedback: Feedback | None = None
    run_started: bool = False
    run_round: int = 0          # last analysis round whose agent run was started
    round_first_seq: int = 1    # first event seq of the current round
    version: int = 0            # optimistic-locking version of the stored row (shared repository)
    reset_generation: str | None = None  # run-local guard; never serialized into the incident


class ConcurrentUpdate(Exception):
    """Another process changed this incident since it was read (the API answers 409 CONFLICT)."""


class LazyEvents(list):
    """An incident's agent events, read from the database only when somebody looks at them."""

    def __init__(self, loader):
        super().__init__()
        self._loader, self._loaded = loader, False

    def _ensure(self):
        if not self._loaded:
            self._loaded = True
            super().extend(self._loader())

    def __iter__(self):
        self._ensure()
        return super().__iter__()

    def __len__(self):
        self._ensure()
        return super().__len__()

    def __bool__(self):
        return len(self) > 0

    def __getitem__(self, i):
        self._ensure()
        return super().__getitem__(i)

    def __reversed__(self):
        self._ensure()
        return super().__reversed__()

    def __contains__(self, x):
        self._ensure()
        return super().__contains__(x)

    def append(self, x):
        self._ensure()
        super().append(x)


class IncidentRepository(Protocol):
    """Storage port for incidents."""

    def next_id(self) -> str: ...
    def add(self, record: IncidentRecord) -> None: ...
    def get(self, incident_id: str) -> IncidentRecord: ...  # KeyError if unknown
    def find_active(self, asset_id: str) -> IncidentRecord | None: ...
    def all(self) -> list[IncidentRecord]: ...
    def clear(self) -> None: ...
    def approved_option(self, incident_id: str, option_id: str) -> Option | None: ...
    def save(self, record: IncidentRecord) -> None: ...
    def append_event(self, record: IncidentRecord, ev: AgentEvent) -> None: ...


class InMemoryIncidentRepository:
    """Thread-safe dict-backed repository. Data is lost on restart."""

    def __init__(self, id_factory, id_reset):
        self._records: dict[str, IncidentRecord] = {}
        self._id_factory, self._id_reset = id_factory, id_reset
        self._lock = threading.RLock()
        self.active_statuses = ACTIVE

    def next_id(self) -> str:
        return self._id_factory()

    def add(self, record: IncidentRecord) -> None:
        with self._lock:
            self._records[record.incident.id] = record

    def get(self, incident_id: str) -> IncidentRecord:
        with self._lock:
            return self._records[incident_id]

    def find_active(self, asset_id: str) -> IncidentRecord | None:
        """First record for the asset whose status is in ACTIVE, else None. Question threads don't count."""
        with self._lock:
            return next((r for r in self._records.values()
                         if r.incident.asset_id == asset_id and r.incident.status in self.active_statuses
                         and not r.incident.question), None)

    def all(self) -> list[IncidentRecord]:
        with self._lock:
            return list(self._records.values())

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._id_reset()

    def approved_option(self, incident_id: str, option_id: str) -> Option | None:
        """Used by WriteAuthorizer: only the option that was actually approved can authorize writes."""
        with self._lock:
            rec = self._records.get(incident_id)
            if not rec or not rec.options or rec.incident.selected_option_id != option_id:
                return None
            if rec.incident.status not in WRITE_OK:
                return None
            return next((o for o in rec.options.options if o.id == option_id), None)

    def save(self, record: IncidentRecord) -> None:
        return None

    def append_event(self, record: IncidentRecord, ev: AgentEvent) -> None:
        with self._lock:
            record.events.append(ev)

    def save_approval(self, record: IncidentRecord, approval: Approval) -> None:
        return None

    def save_feedback(self, record: IncidentRecord, fb: Feedback, technician_id: str | None, author: str | None) -> None:
        return None


class SqlIncidentRepository(InMemoryIncidentRepository):
    """Write-through cache over it.db. Everything is loaded at start-up, so reads stay in memory."""

    def __init__(self, db):
        self._db = db
        super().__init__(lambda: f"INC-{db.next_id('INC', 42):04d}", lambda: None)
        self.active_statuses = ACTIVE_UNTIL_CLOSED
        for row in db.q("SELECT * FROM incidents ORDER BY created_at"):
            inc = Incident.model_validate_json(row["payload"])
            rec = IncidentRecord(inc, run_started=row["run_state"] != "pending",
                                 run_round=inc.round if row["run_state"] != "pending" else inc.round - 1)
            rec.events = [AgentEvent.model_validate_json(e["payload"])
                          for e in db.q("SELECT payload FROM incident_events WHERE incident_id=? ORDER BY seq", (inc.id,))]
            first = db.val("SELECT MIN(seq) FROM incident_events WHERE incident_id=? AND round=?", (inc.id, inc.round))
            rec.round_first_seq = first or ((rec.events[-1].seq + 1) if rec.events else 1)
            opt = db.one("SELECT payload FROM incident_options WHERE incident_id=? AND round=?", (inc.id, inc.round))
            rec.options = OptionsResponse.model_validate_json(opt["payload"]) if opt else None
            apr = db.one("SELECT payload FROM approvals WHERE incident_id=? AND round=? AND decision='approve' "
                         "ORDER BY approved_at DESC LIMIT 1", (inc.id, inc.round))
            rec.approval = Approval.model_validate_json(apr["payload"]) if apr else None
            fb = db.one("SELECT payload FROM feedback WHERE incident_id=? ORDER BY created_at DESC LIMIT 1", (inc.id,))
            rec.feedback = Feedback.model_validate_json(fb["payload"]) if fb else None
            self._records[inc.id] = rec

    def add(self, record: IncidentRecord) -> None:
        super().add(record)
        self.save(record)

    def save(self, record: IncidentRecord) -> None:
        inc = record.incident
        run_state = "pending" if record.run_round < inc.round else "started"
        with self._lock:
            self._db.x(
                "INSERT INTO incidents(id, asset_id, plant_id, line_id, status, trigger, parent_id, round, "
                "assigned_technician_id, work_order_id, approved_by, created_at, updated_at, resolved_at, payload, run_state) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "round=excluded.round, assigned_technician_id=excluded.assigned_technician_id, "
                "work_order_id=excluded.work_order_id, approved_by=excluded.approved_by, updated_at=excluded.updated_at, "
                "resolved_at=excluded.resolved_at, payload=excluded.payload, run_state=excluded.run_state",
                (inc.id, inc.asset_id, inc.plant_id, inc.line_id, inc.status.value, inc.trigger.value, inc.parent_id,
                 inc.round, inc.assigned_technician_id, inc.work_order_id, inc.approved_by,
                 inc.created_at.isoformat(), inc.updated_at.isoformat(),
                 inc.resolved_at.isoformat() if inc.resolved_at else None, inc.model_dump_json(), run_state))
            if record.options is not None:
                self._db.x("INSERT INTO incident_options(incident_id, round, payload, created_at) VALUES(?,?,?,?) "
                           "ON CONFLICT(incident_id, round) DO UPDATE SET payload=excluded.payload",
                           (inc.id, inc.round, record.options.model_dump_json(), inc.updated_at.isoformat()))

    def append_event(self, record: IncidentRecord, ev: AgentEvent) -> None:
        super().append_event(record, ev)
        self._db.x("INSERT INTO incident_events(incident_id, seq, round, ts, agent, type, payload) "
                   "VALUES(?,?,?,?,?,?,?) ON CONFLICT(incident_id, seq) DO UPDATE SET round=excluded.round, ts=excluded.ts, "
                   "agent=excluded.agent, type=excluded.type, payload=excluded.payload", (ev.incident_id, ev.seq, ev.round or record.incident.round,
                                             ev.ts.isoformat(), ev.agent.value, ev.type.value, ev.model_dump_json()))

    def save_approval(self, record: IncidentRecord, approval: Approval) -> None:
        self._db.x("INSERT INTO approvals(id, incident_id, round, option_id, decision, approver_id, approved_at, "
                   "payload) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET decision=excluded.decision, "
                   "approver_id=excluded.approver_id, approved_at=excluded.approved_at, payload=excluded.payload",
                   (approval.id, approval.incident_id, record.incident.round, approval.option_id, approval.decision.value,
                    approval.approver_id, approval.approved_at.isoformat(), approval.model_dump_json()))

    def save_feedback(self, record: IncidentRecord, fb: Feedback, technician_id: str | None, author: str | None) -> None:
        self._db.x("INSERT INTO feedback(id, incident_id, technician_id, author_id, payload, created_at) "
                   "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (fb.id, fb.incident_id, technician_id, author, fb.model_dump_json(),
                                           fb.created_at.isoformat()))

    def clear(self) -> None:  # the simulated plant is never wiped from the API
        return None


def dumps(o) -> str:
    return json.dumps(o, default=str)


class SharedIncidentRepository(SqlIncidentRepository):
    """For several processes (api x N, engine, worker x N) on PostgreSQL: nothing is cached between calls; every read
    comes from the database and every save is checked against the row's version (optimistic locking), so two
    processes can never silently overwrite each other's change."""

    def __init__(self, db):
        self._db = db
        InMemoryIncidentRepository.__init__(self, lambda: f"INC-{db.next_id('INC', 42):04d}", lambda: None)
        self.active_statuses = ACTIVE_UNTIL_CLOSED

    def _events(self, incident_id: str):
        return lambda: [AgentEvent.model_validate_json(e["payload"]) for e in
                        self._db.q("SELECT payload FROM incident_events WHERE incident_id=? ORDER BY seq", (incident_id,))]

    def _record(self, row) -> IncidentRecord:
        db = self._db
        inc = Incident.model_validate_json(row["payload"])
        started = row["run_state"] != "pending"
        rec = IncidentRecord(inc, run_started=started, run_round=inc.round if started else inc.round - 1,
                             version=row["version"])
        rec.events = LazyEvents(self._events(inc.id))
        first = db.val("SELECT MIN(seq) FROM incident_events WHERE incident_id=? AND round=?", (inc.id, inc.round))
        last = db.val("SELECT MAX(seq) FROM incident_events WHERE incident_id=?", (inc.id,), 0)
        rec.round_first_seq = first or (last + 1)
        opt = db.one("SELECT payload FROM incident_options WHERE incident_id=? AND round=?", (inc.id, inc.round))
        rec.options = OptionsResponse.model_validate_json(opt["payload"]) if opt else None
        apr = db.one("SELECT payload FROM approvals WHERE incident_id=? AND round=? AND decision='approve' "
                     "ORDER BY approved_at DESC LIMIT 1", (inc.id, inc.round))
        rec.approval = Approval.model_validate_json(apr["payload"]) if apr else None
        fb = db.one("SELECT payload FROM feedback WHERE incident_id=? ORDER BY created_at DESC LIMIT 1", (inc.id,))
        rec.feedback = Feedback.model_validate_json(fb["payload"]) if fb else None
        return rec

    def get(self, incident_id: str) -> IncidentRecord:
        row = self._db.one("SELECT payload, run_state, version FROM incidents WHERE id=?", (incident_id,))
        if row is None:
            raise KeyError(incident_id)
        return self._record(row)

    def all(self) -> list[IncidentRecord]:
        return [self._record(r) for r in self._db.q("SELECT payload, run_state, version FROM incidents ORDER BY created_at")]

    def find_active(self, asset_id: str) -> IncidentRecord | None:
        marks = ",".join("?" * len(self.active_statuses))
        for row in self._db.q(f"SELECT payload, run_state, version FROM incidents WHERE asset_id=? AND status IN ({marks}) "
                              "ORDER BY created_at", (asset_id, *[s.value for s in self.active_statuses])):
            rec = self._record(row)
            if not rec.incident.question:
                return rec
        return None

    def approved_option(self, incident_id: str, option_id: str) -> Option | None:
        try:
            rec = self.get(incident_id)
        except KeyError:
            return None
        if not rec.options or rec.incident.selected_option_id != option_id or rec.incident.status not in WRITE_OK:
            return None
        return next((o for o in rec.options.options if o.id == option_id), None)

    def add(self, record: IncidentRecord) -> None:
        inc = record.incident
        run_state = "pending" if record.run_round < inc.round else "started"
        self._db.x("INSERT INTO incidents(id, asset_id, plant_id, line_id, status, trigger, parent_id, round, "
                   "assigned_technician_id, work_order_id, approved_by, created_at, updated_at, resolved_at, payload, "
                   "run_state, version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                   (inc.id, inc.asset_id, inc.plant_id, inc.line_id, inc.status.value, inc.trigger.value, inc.parent_id,
                    inc.round, inc.assigned_technician_id, inc.work_order_id, inc.approved_by, inc.created_at.isoformat(),
                    inc.updated_at.isoformat(), inc.resolved_at.isoformat() if inc.resolved_at else None,
                    inc.model_dump_json(), run_state))
        record.version = 1
        if not isinstance(record.events, LazyEvents):
            for ev in list(record.events):
                self.append_event(record, ev, keep=False)

    def save(self, record: IncidentRecord) -> None:
        inc = record.incident
        run_state = "pending" if record.run_round < inc.round else "started"
        n = self._db.xc(
            "UPDATE incidents SET status=?, round=?, assigned_technician_id=?, work_order_id=?, approved_by=?, "
            "updated_at=?, resolved_at=?, payload=?, run_state=?, version=version+1 WHERE id=? AND version=?",
            (inc.status.value, inc.round, inc.assigned_technician_id, inc.work_order_id, inc.approved_by,
             inc.updated_at.isoformat(), inc.resolved_at.isoformat() if inc.resolved_at else None, inc.model_dump_json(),
             run_state, inc.id, record.version))
        if n == 0:
            raise ConcurrentUpdate(inc.id)
        record.version += 1
        if record.options is not None:
            self._db.x("INSERT INTO incident_options(incident_id, round, payload, created_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(incident_id, round) DO UPDATE SET payload=excluded.payload",
                       (inc.id, inc.round, record.options.model_dump_json(), inc.updated_at.isoformat()))

    def append_event(self, record: IncidentRecord, ev: AgentEvent, keep: bool = True) -> None:
        if keep:
            record.events.append(ev)
        self._db.x("INSERT INTO incident_events(incident_id, seq, round, ts, agent, type, payload) "
                   "VALUES(?,?,?,?,?,?,?) ON CONFLICT(incident_id, seq) DO UPDATE SET round=excluded.round, ts=excluded.ts, "
                   "agent=excluded.agent, type=excluded.type, payload=excluded.payload",
                   (ev.incident_id, ev.seq, ev.round or record.incident.round, ev.ts.isoformat(), ev.agent.value,
                    ev.type.value, ev.model_dump_json()))
