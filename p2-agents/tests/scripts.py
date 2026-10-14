"""Scripted model behaviours for tests: they act like a real tool-calling model, turn by turn, so the whole harness
(streaming, tool loop, sub-agents, hand-offs, submit, redaction) is exercised without network."""
from __future__ import annotations

import json
import re

from llm.providers.scripted import Turn
from llm.types import LLMRequest

PLACEHOLDER = re.compile(r"<PERSON_NAME_\d+>")


def step(req: LLMRequest) -> int:
    """How many assistant turns this agent already had in this run."""
    return sum(1 for m in req.messages if m["role"] == "assistant")


def tool_names(req: LLMRequest) -> set[str]:
    return {t["function"]["name"] for t in req.tools}


def story(req: LLMRequest) -> Turn:
    """INC-0042 as a good model would play it."""
    a, n = req.agent, step(req)
    if a == "orchestrator":
        if "team has finished" in req.messages[0]["content"]:
            return Turn(reasoning="The decision is ready; summarise for the operator.",
                        text="EQ-3012's rear bearing is wearing (60% sure). Option B: run at 70% and swap it Wednesday "
                             "night for about €2,485, meeting Friday's deadline. A maintenance lead must approve.")
        return Turn(reasoning="ML alert with high risk. Standard investigation order applies.",
                    text="ML alert on EQ-3012 (risk 78%). Plan: confirm signal, diagnose, find part, schedule, energy, decide.")
    if a == "monitor":
        return [Turn(reasoning="First the ML report.", text="Checking the ML health report first.",
                     tool_calls=[("get_asset_health", {"asset_id": "EQ-3012"})]),
                Turn(text="Now the vibration trend behind it.",
                     tool_calls=[("get_signals", {"asset_id": "EQ-3012", "signals": ["vibration_rms"], "minutes": 360})]),
                Turn(text="Risk is real and the rule has not fired yet.",
                     tool_calls=[("submit_result", {"summary": "Vibration +34% in 6 h, failure likely within 3 days"})]),
                ][min(n, 2)]
    if a == "diagnosis":
        return [Turn(reasoning="Let the researcher dig through manuals while I check history.",
                     text="Asking the manual researcher for the matching manual section.",
                     tool_calls=[("ask_manual_researcher", {"task": "Find manual guidance for rising spindle vibration "
                                                                     "with high kurtosis on EQ-3012."})]),
                Turn(text="Checking past failures and quality on the line.",
                     tool_calls=[("get_maintenance_history", {"asset_id": "EQ-3012"}),
                                 ("get_defects", {"line_id": "L3", "hours": 24})]),
                Turn(text="Naming the exact part.", tool_calls=[("get_bom", {"asset_id": "EQ-3012"})]),
                Turn(text="Kurtosis 5.1 points to bearing damage, but misalignment can't be ruled out without inspection.",
                     tool_calls=[("submit_result", {
                         "root_cause": "Rear spindle bearing (SKF-6205) wear", "failure_mode": "bearing_wear",
                         "alternatives": ["Spindle misalignment (manual §7.6) — kurtosis argues against it"],
                         "required_parts": ["SKF-6205", "NOT-IN-BOM"]})]),
                ][min(n, 3)]
    if a == "manual_researcher":
        if n == 0:
            return Turn(text="Searching the spindle manual.",
                        tool_calls=[("search_manuals", {"query": "spindle vibration rising bearing", "asset_id": "EQ-3012"})])
        return Turn(text="Manual MAN-SPINDLE-200 §7.4: RMS +30% in 24 h with kurtosis > 4 means inspect the rear bearing.")
    if a == "spare_parts":
        if n == 0:
            return Turn(text="Checking stock of the bearing.", tool_calls=[("check_stock", {"sku": "SKF-6205"})])
        return Turn(text="Plant A has none; Plant B has 4. The supplier needs 21 days.")
    if a == "scheduler":
        if n == 0:
            return Turn(text="Reading the order book and who can swap a bearing.",
                        tool_calls=[("get_orders", {"line_id": "L3"}),
                                    ("find_technicians", {"skill": "bearing_replacement"})])
        names = PLACEHOLDER.findall(" ".join(m.get("content") or "" for m in req.messages))
        who = names[0] if names else "a technician"
        return Turn(text=f"{who} can do the swap on the night shift; at 70% we still finish Friday.")
    if a == "energy":
        if n == 0:
            return Turn(text="Reading line energy.", tool_calls=[("get_energy", {"line_id": "L3"})])
        return Turn(text="EQ-3012 draws 8% above baseline - friction from the worn bearing.")
    if a == "decision":
        return Turn(reasoning="Compare B with A and C.",
                    text="Option B meets Friday at ~€2.5k vs €22k for A. Confidence 60% → human must approve.")
    return Turn(text="ok")


def question_handoff(req: LLMRequest) -> Turn:
    """Orchestrator diverts a stock question to Spare parts, which answers it."""
    a, n = req.agent, step(req)
    if a == "orchestrator":
        return Turn(reasoning="This is a stock question.", text="Spare parts knows this best.",
                    tool_calls=[("handoff_task", {"agent": "spare_parts", "task": "Is a spare SKF-6205 bearing available?",
                                                  "reason": "stock question"})])
    if a == "spare_parts":
        if n == 0:
            return Turn(text="Checking stock.", tool_calls=[("check_stock", {"sku": "SKF-6205"})])
        return Turn(reasoning="Plant A 0, Plant B 4.", text="Not in Plant A, but Plant B has 4. A standard transfer takes about 20 h.")
    return Turn(text="?")


def question_subagent(req: LLMRequest) -> Turn:
    """Orchestrator asks Monitor as a sub-agent, then answers itself."""
    a, n = req.agent, step(req)
    if a == "orchestrator":
        if n == 0:
            return Turn(text="Asking the monitor.", tool_calls=[("ask_monitor", {"task": "Why is EQ-3012 red?"})])
        tool_msgs = [m for m in req.messages if m["role"] == "tool"]
        answer = json.loads(tool_msgs[-1]["content"])["answer"]
        return Turn(text=f"The monitor says: {answer}")
    if a == "monitor":
        if n == 0:
            return Turn(text="Checking health.", tool_calls=[("get_asset_health", {"asset_id": "EQ-3012"})])
        return Turn(text="Failure risk is 78% within 72 h, driven by vibration +34% in 6 h.")
    return Turn(text="?")


def failing(req: LLMRequest) -> Turn:
    return Turn(fail=True)


def lazy(req: LLMRequest) -> Turn:
    """A model that never calls tools - the harness must still fetch required data."""
    return Turn(text="Looks fine to me.")


# ---- providers usable from an .ini ([llm] provider = scripts:StoryProvider) for backend integration tests ----
from llm.providers.scripted import ScriptedProvider  # noqa: E402


class StoryProvider(ScriptedProvider):
    def __init__(self):
        super().__init__(story)


class QuestionProvider(ScriptedProvider):
    def __init__(self):
        super().__init__(question_handoff)


class FailingProvider(ScriptedProvider):
    def __init__(self):
        super().__init__(failing)
