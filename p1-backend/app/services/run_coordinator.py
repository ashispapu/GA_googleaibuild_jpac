"""RunCoordinator: runs the agent runtime for an incident, records every event, and fans it out on the bus.
Viewers (WebSocket) follow the recorded log, then live events, so late joiners and reconnects see everything."""
import asyncio
import logging
from typing import AsyncIterator

from plantguard_contracts.interfaces import AuditLogger, EventBus
from plantguard_contracts.schemas.incidents import (Agent, AgentEvent, Diagnosis, EventType, IncidentStatus,
                                                    OptionsResponse)

from app.core.common import Clock, utcnow
from app.repositories.incident_repository import IncidentRepository
from app.services.agent_runtime import RuntimeFactory

log = logging.getLogger("plantguard.runner")
IDLE_TIMEOUT_S = 120.0


def is_final(ev: AgentEvent) -> bool:
    """True for the orchestrator's `finished` status event, which ends a run."""
    return ev.agent == Agent.ORCHESTRATOR and ev.type == EventType.STATUS and ev.message == "finished"


class RunCoordinator:
    """Runs an incident's agent runtime once, stores each event and publishes it on the bus."""

    def __init__(self, repo: IncidentRepository, bus: EventBus, runtime_factory: RuntimeFactory,
                 audit: AuditLogger, clock: Clock = utcnow):
        """repo: where events are stored; bus: live fan-out; runtime_factory: speed -> runtime; audit/clock: failure
        logging and timestamps."""
        self._repo, self._bus, self._factory, self._audit, self._clock = repo, bus, runtime_factory, audit, clock
        self._tasks: dict[str, asyncio.Task] = {}

    def ensure_started(self, incident_id: str, speed: float = 1.0) -> None:
        """Start the run as a background task if it has not started (first WebSocket viewer wins; later viewers only
        watch)."""
        rec = self._repo.get(incident_id)
        if rec.run_started:
            return
        rec.run_started = True
        self._tasks[incident_id] = asyncio.create_task(self._run(incident_id, speed))

    async def _run(self, incident_id: str, speed: float) -> None:
        """Drive the runtime and record every event. If it crashes, audit it and emit an ERROR plus `finished` so viewers
        never hang."""
        rec = self._repo.get(incident_id)
        try:
            async for ev in self._factory(speed).run(rec.incident):
                await self._record(rec, ev)
        except Exception as e:
            log.exception("agent run failed for %s", incident_id)
            self._audit.log("system", "orchestrator", "agent_run_failed", incident_id, {"error": type(e).__name__})
            last = rec.events[-1].seq if rec.events else 0
            await self._record(rec, AgentEvent(incident_id=incident_id, seq=last + 1, ts=self._clock(),
                                               agent=Agent.ORCHESTRATOR, type=EventType.ERROR,
                                               message="Agent run failed; ask a human to review"))
            await self._record(rec, AgentEvent(incident_id=incident_id, seq=last + 2, ts=self._clock(),
                                               agent=Agent.ORCHESTRATOR, type=EventType.STATUS, message="finished"))

    async def _record(self, rec, ev: AgentEvent) -> None:
        """Store the event, copy a Diagnosis/OptionsResponse result onto the incident, flip ANALYSING ->
        AWAITING_APPROVAL on `finished`, then publish event (and status)."""
        rec.events.append(ev)
        inc = rec.incident
        if ev.type == EventType.RESULT:
            if isinstance(ev.result, Diagnosis):
                inc.diagnosis = ev.result
            elif isinstance(ev.result, OptionsResponse):
                rec.options = ev.result
        status_changed = False
        if is_final(ev) and rec.options and inc.status == IncidentStatus.ANALYSING:
            inc.status, status_changed = IncidentStatus.AWAITING_APPROVAL, True
        inc.updated_at = self._clock()
        await self._bus.publish(f"incident.{inc.id}.events", ev)
        if status_changed:
            await self._bus.publish(f"incident.{inc.id}.status", inc)

    def events(self, incident_id: str, after_seq: int = 0) -> list[AgentEvent]:
        """Stored events with seq greater than after_seq (REST catch-up)."""
        return [e for e in self._repo.get(incident_id).events if e.seq > after_seq]

    async def follow(self, incident_id: str, after_seq: int = 0, idle_timeout_s: float = IDLE_TIMEOUT_S
                     ) -> AsyncIterator[AgentEvent]:
        """History after `after_seq`, then live events, until the orchestrator reports `finished`."""
        sub = self._bus.subscribe(f"incident.{incident_id}.events")  # subscribe first: nothing can slip between
        try:
            last = after_seq
            for ev in self.events(incident_id, after_seq):
                last = ev.seq
                yield ev
                if is_final(ev):
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
                if is_final(ev):
                    return
        finally:
            sub.close()
