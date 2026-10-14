"""Manual researcher: a HELPER sub-agent of Diagnosis. It searches manuals and past work orders under Diagnosis'
identity (principal = diagnosis, so the C4 allow-list still applies) and reports cited findings back.
It has no typed RESULT and never appears in a flow on its own."""
from harness.base_agent import BaseAgent


class ManualResearcherAgent(BaseAgent):
    result_modes = frozenset()
    thought_modes = frozenset()  # its final text is the report -> MESSAGE (shown under Diagnosis as a sub-agent)
