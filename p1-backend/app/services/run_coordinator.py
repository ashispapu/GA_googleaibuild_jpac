"""RunCoordinator: runs the agent runtime for an incident, records every event, and fans it out on the bus.
Viewers (WebSocket) follow the recorded log, then live events, so late joiners and reconnects see everything.
An incident can be analysed more than once (round 2+ after a repair that did not work): event seq numbers keep
counting, every event carries its round, and a stream ends when the CURRENT round's orchestrator says `finished`."""
import asyncio
import logging
from typing import AsyncIterator, Callable

from plantguard_contracts.interfaces import AuditLogger, EventBus
from plantguard_contracts.schemas.incidents import (Agent, AgentEvent, Diagnosis, EventType, IncidentStatus,
                                                    OptionsResponse)

from app.core.common import Clock, utcnow
from app.repositories.incident_repository import IncidentRecord, IncidentRepository
from app.services.agent_runtime import RuntimeFactory

log = logging.getLogger("plantguard.runner")
IDLE_TIMEOUT_S = 120.0


def is_final(ev: AgentEvent) -> bool:
    """True for the orchestrator's `finished` status event, which ends a run."""
    return ev.agent == Agent.ORCHESTRATOR and ev.type == EventType.STATUS and ev.message == "finished" and not ev.sub_agent


class RunCoordinator:
    """Runs an incident's agent runtime once per analysis round, stores each event and publishes it on the bus."""

    def __init__(self, repo: IncidentRepository, bus: EventBus, runtime_factory: RuntimeFactory,
                 audit: AuditLogger, clock: Clock = utcnow):
        self._repo, self._bus, self._factory, self._audit, self._clock = repo, bus, runtime_factory, audit, clock
        self._tasks: dict[str, asyncio.Task] = {}
        self.listeners: list[Callable[[IncidentRecord, AgentEvent], None]] = []  # e.g. the Garden
        self.on_options_ready: list[Callable[[IncidentRecord], object]] = []

    def ensure_started(self, incident_id: str, speed: float = 1.0) -> None:
        """Start the current round's run as a background task if it has not started (first viewer or the incident
        service wins; later callers only watch)."""
        rec = self._repo.get(incident_id)
        rnd = rec.incident.round
        if rec.run_round >= rnd:
            return
        if rec.incident.status != IncidentStatus.ANALYSING:
            return
        rec.run_started, rec.run_round = True, rnd
        rec.round_first_seq = (rec.events[-1].seq + 1) if rec.events else 1
        self._repo.save(rec)
        self._tasks[f"{incident_id}#{rnd}"] = asyncio.create_task(self._run(incident_id, speed))

    def running(self, incident_id: str) -> bool:
        return any(not t.done() for k, t in self._tasks.items() if k.startswith(f"{incident_id}#"))

    async def _run(self, incident_id: str, speed: float) -> None:
        """Drive the runtime and record every event (re-numbered after earlier rounds). If it crashes, audit it and
        emit an ERROR plus `finished` so viewers never hang."""
        rec = self._repo.get(incident_id)
        offset = rec.round_first_seq - 1
        try:
            async for ev in self._factory(speed).run(rec.incident):
                await self._record(rec, ev.model_copy(update={"seq": ev.seq + offset, "round": rec.incident.round}))
        except Exception as e:
            log.exception("agent run failed for %s", incident_id)
            self._audit.log("system", "orchestrator", "agent_run_failed", incident_id, {"error": type(e).__name__,
                                                                                    "incident_id": incident_id})
            last = rec.events[-1].seq if rec.events else offset
            await self._record(rec, AgentEvent(incident_id=incident_id, seq=last + 1, ts=self._clock(),
                                               agent=Agent.ORCHESTRATOR, type=EventType.ERROR, round=rec.incident.round,
                                               message="Agent run failed; ask a human to review"))
            await self._record(rec, AgentEvent(incident_id=incident_id, seq=last + 2, ts=self._clock(),
                                               agent=Agent.ORCHESTRATOR, type=EventType.STATUS, round=rec.incident.round,
                                               message="finished"))

    async def _record(self, rec: IncidentRecord, ev: AgentEvent) -> None:
        """Store the event, copy a Diagnosis/OptionsResponse result onto the incident, flip ANALYSING ->
        AWAITING_APPROVAL on `finished`, then publish event (and status)."""
        self._repo.append_event(rec, ev)
        inc = rec.incident
        if ev.type == EventType.RESULT:
            if isinstance(ev.result, Diagnosis):
                inc.diagnosis = ev.result
            elif isinstance(ev.result, OptionsResponse):
                rec.options = ev.result
        status_changed = False
        if is_final(ev) and inc.status == IncidentStatus.ANALYSING:
            if rec.options:
                inc.status, status_changed = IncidentStatus.AWAITING_APPROVAL, True
            elif inc.question:
                inc.status, status_changed = IncidentStatus.RESOLVED, True  # a question thread is done when answered
                inc.resolved_at = self._clock()
        inc.updated_at = self._clock()
        if status_changed or ev.type == EventType.RESULT or is_final(ev):
            self._repo.save(rec)
        for fn in self.listeners:
            try:
                fn(rec, ev)
            except Exception:
                log.exception("event listener failed")
        await self._bus.publish(f"incident.{inc.id}.events", ev)
        if status_changed:
            await self._bus.publish(f"incident.{inc.id}.status", inc)
            if inc.status == IncidentStatus.AWAITING_APPROVAL:
                for fn in self.on_options_ready:
                    fn(rec)

    def events(self, incident_id: str, after_seq: int = 0) -> list[AgentEvent]:
        """Stored events with seq greater than after_seq (REST catch-up)."""
        return [e for e in self._repo.get(incident_id).events if e.seq > after_seq]

    def _round_finished(self, rec: IncidentRecord) -> bool:
        return any(is_final(e) and e.seq >= rec.round_first_seq for e in rec.events) or (
            rec.incident.status != IncidentStatus.ANALYSING and not self.running(rec.incident.id))

    async def follow(self, incident_id: str, after_seq: int = 0, idle_timeout_s: float = IDLE_TIMEOUT_S
                     ) -> AsyncIterator[AgentEvent]:
        """History after `after_seq`, then live events, until the current round's orchestrator reports `finished`."""
        sub = self._bus.subscribe(f"incident.{incident_id}.events")  # subscribe first: nothing can slip between
        try:
            rec = self._repo.get(incident_id)
            last = after_seq
            for ev in self.events(incident_id, after_seq):
                last = ev.seq
                yield ev
            if self._round_finished(rec):
                return
            it = sub.__aiter__()
            while True:
                try:
                    payload = await asyncio.wait_for(it.__anext__(), idle_timeout_s)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    return
                ev = AgentEvent.model_validate(payload)
                if ev.seq <= last:
                    continue
                last = ev.seq
                yield ev
                if is_final(ev) and ev.seq >= rec.round_first_seq:
                    return
        finally:
            sub.close()
