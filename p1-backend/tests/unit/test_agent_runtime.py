import asyncio
from datetime import datetime, timezone

from app.core.config import Settings
from app.services.agent_runtime import FallbackAgentRuntime, ReplayAgentRuntime
from plantguard_contracts.schemas.incidents import Incident, IncidentStatus, IncidentTrigger, Severity


def test_fallback_runtime_switches_to_replay_on_failure():
    """When the primary fails, an error event is emitted and the replay continues with rising seq numbers."""
    class Broken:
        async def run(self, incident):
            raise RuntimeError("llm down")
            yield  # pragma: no cover
        async def on_feedback(self, *a): ...
        async def on_approval(self, *a): ...

    async def nosleep(_): ...
    now = datetime.now(timezone.utc)
    inc = Incident(id="INC-0043", title="t", plant_id="PLANT-A", line_id="L3", asset_id="EQ-3012",
                   trigger=IncidentTrigger.ML_ALERT, severity=Severity.HIGH, status=IncidentStatus.ANALYSING,
                   created_at=now, updated_at=now)
    rt = FallbackAgentRuntime(Broken(), ReplayAgentRuntime(Settings().agent_events_file, sleep=nosleep))
    async def collect():
        return [e async for e in rt.run(inc)]
    evs = asyncio.run(collect())
    assert evs[0].type.value == "error" and "LLM_UNAVAILABLE" in evs[0].message
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    assert all(e.incident_id == "INC-0043" for e in evs)
    assert evs[-1].message == "finished"
