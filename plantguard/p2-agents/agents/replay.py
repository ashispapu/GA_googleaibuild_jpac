"""Replay fallback (AG-8): streams the recorded run. Ship this FIRST — it is the demo safety net."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from plantguard_contracts.schemas.incidents import AgentEvent

RUN = Path(__file__).resolve().parents[2] / "fixtures" / "story" / "agent_events_INC-0042.jsonl"


async def replay(incident_id: str, speed: float = 1.0):
    prev = None
    for line in RUN.read_text().splitlines():
        e = AgentEvent.model_validate_json(line)
        if prev:
            await asyncio.sleep((e.ts - prev).total_seconds() / speed)
        prev = e.ts
        yield e.model_copy(update={"incident_id": incident_id, "ts": datetime.now(timezone.utc)})
