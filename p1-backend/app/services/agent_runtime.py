"""Agent runtimes (C3). ReplayAgentRuntime is the demo safety net until P2's real runtime is plugged in;
FallbackAgentRuntime switches to it when the primary fails (e.g. LLM unavailable)."""
import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, Callable

from plantguard_contracts.interfaces import AgentRuntime
from plantguard_contracts.schemas.approvals import Approval, Feedback
from plantguard_contracts.schemas.incidents import Agent, AgentEvent, EventType, Incident

from app.core.common import Clock, utcnow

log = logging.getLogger("plantguard.runtime")
RECORDED_INCIDENT_ID = "INC-0042"

RuntimeFactory = Callable[[float], AgentRuntime]  # speed -> runtime


class ReplayAgentRuntime:  # implements interfaces.AgentRuntime
    """Replays the recorded INC-0042 agent run (agent_events_INC-0042.jsonl) as if agents were working."""

    def __init__(self, events_file: Path, speed: float = 1.0, clock: Clock = utcnow, sleep=asyncio.sleep):
        """events_file: the recording; speed: playback speed (2.0 = twice as fast); clock/sleep: injectable for tests.
        events_file: the recording; speed: playback speed (2.0 = twice as fast); clock/sleep: injectable for tests."""
        self._lines = events_file.read_text().splitlines()
        self._speed, self._clock, self._sleep = max(speed, 0.01), clock, sleep

    async def run(self, incident: Incident) -> AsyncIterator[AgentEvent]:
        """Yield the recorded events, re-keyed to this incident, waiting the original gap between events (divided by
        speed) and stamping each with the current time."""
        prev = None
        for line in self._lines:
            # the recording is for INC-0042; re-key every id in it (events, option drafts) to this incident
            ev = AgentEvent.model_validate_json(line.replace(RECORDED_INCIDENT_ID, incident.id))
            if prev is not None:
                await self._sleep(max(0.0, (ev.ts - prev).total_seconds()) / self._speed)
            prev = ev.ts
            yield ev.model_copy(update={"ts": self._clock()})

    async def on_feedback(self, incident: Incident, feedback: Feedback) -> None:
        """Nothing to learn from in a replay."""
        return None

    async def on_approval(self, incident: Incident, approval: Approval) -> None:
        """Nothing to do in a replay."""
        return None


class FallbackAgentRuntime:  # implements interfaces.AgentRuntime
    """Uses the primary runtime; if it raises, emits an ERROR event and continues with the fallback."""

    def __init__(self, primary: AgentRuntime, fallback: AgentRuntime, clock: Clock = utcnow):
        """primary: the real runtime; fallback: the replay; clock: for the error event."""
        self._primary, self._fallback, self._clock = primary, fallback, clock

    async def run(self, incident: Incident) -> AsyncIterator[AgentEvent]:
        """Stream the primary's events; on failure emit LLM_UNAVAILABLE and stream the fallback's events with sequence
        numbers continuing after the last one sent."""
        last = 0
        try:
            async for ev in self._primary.run(incident):
                last = ev.seq
                yield ev
            return
        except Exception as e:
            log.warning("primary agent runtime failed (%s); replaying recorded run", e)
            last += 1
            yield AgentEvent(incident_id=incident.id, seq=last, ts=self._clock(), agent=Agent.ORCHESTRATOR,
                             type=EventType.ERROR, message="LLM_UNAVAILABLE: running from recorded analysis")
        async for ev in self._fallback.run(incident):
            yield ev.model_copy(update={"seq": last + ev.seq})

    async def on_feedback(self, incident: Incident, feedback: Feedback) -> None:
        """Forward feedback to the primary runtime."""
        await self._primary.on_feedback(incident, feedback)

    async def on_approval(self, incident: Incident, approval: Approval) -> None:
        """Forward the approval to the primary runtime."""
        await self._primary.on_approval(incident, approval)
