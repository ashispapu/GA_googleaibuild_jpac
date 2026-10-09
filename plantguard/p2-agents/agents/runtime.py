"""AgentRuntime (AG-4). Backend calls run(incident) and streams every event to the UI.
Order: orchestrator -> monitor -> diagnosis -> spare_parts -> scheduler -> energy -> decision -> finished.
Each agent: STATUS started, 1-6 THOUGHT/TOOL_CALL/TOOL_RESULT/EVIDENCE, exactly one RESULT.
Compare your output with ../../fixtures/story/agent_events_INC-0042.jsonl — same shape."""
from typing import AsyncIterator

from plantguard_contracts.schemas.incidents import AgentEvent, Incident


class PlantAgentRuntime:  # implements interfaces.AgentRuntime
    def __init__(self, tool_client, llm, redactor, retriever):
        self.tools, self.llm, self.redactor, self.retriever = tool_client, llm, redactor, retriever

    async def run(self, incident: Incident) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError  # AG-4; until then use replay.py

    async def on_feedback(self, incident, feedback) -> None:
        raise NotImplementedError  # AG-9

    async def on_approval(self, incident, approval) -> None:
        pass
