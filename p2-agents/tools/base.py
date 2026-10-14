"""Tool abstraction shared by every kind of tool the agents can be given.

- `plant`  tools wrap a contract ToolSpec (C4) and go through the ToolGateway (allow-list, validation, audit).
- `agent`  tools are harness controls: ask a sub-agent, hand a task off, submit a structured result.

Agents never construct tools; the runtime builds the list from the .ini and passes it to `agent.run(..., tools=...)`.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from plantguard_contracts.schemas.incidents import Evidence

if TYPE_CHECKING:  # pragma: no cover
    from harness.context import RunContext


@dataclass
class Handoff:
    """Control signal: the task is diverted to another agent; the current agent stops."""
    target: str
    task: str
    reason: str = ""
    origin: str = ""


@dataclass
class Finish:
    """Control signal: the LLM handed back its structured fields; the current agent stops."""
    payload: dict[str, Any]


@dataclass
class ToolOutcome:
    content: str                       # what the model reads back (compact, redacted later by the LLM client)
    data: Any = None                   # structured result (contract output as JSON-able dict)
    summary: str = ""                  # TOOL_RESULT message for the UI ("" = no TOOL_RESULT event)
    evidence: list[Evidence] = field(default_factory=list)
    evidence_message: str = ""         # EVIDENCE message for the UI
    control: Handoff | Finish | None = None
    is_error: bool = False
    inherited_evidence: list[Evidence] = field(default_factory=list)  # already shown by a sub-agent; kept, not re-emitted


@dataclass
class ToolInvocation:
    """Who is calling, on whose behalf, inside which run."""
    ctx: "RunContext"
    agent: str            # agent (folder) name
    principal: str        # contract identity (allow-list + AgentEvent.agent)
    sub_agent: str | None  # set for helper agents
    depth: int = 0


class Tool(ABC):
    name: str = ""
    description: str = ""
    kind: Literal["plant", "agent"] = "agent"

    @abstractmethod
    def parameters(self) -> dict:
        """JSON schema of the arguments (type=object)."""

    def schema(self) -> dict:
        """OpenAI/LiteLLM function-calling schema."""
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters()}}

    def call_label(self, args: dict) -> str:
        """Plain-language TOOL_CALL message."""
        return self.name.replace("_", " ").capitalize()

    @abstractmethod
    async def run(self, args: dict, inv: ToolInvocation) -> ToolOutcome:
        ...


def to_content(data: Any, limit: int) -> str:
    """Compact JSON for the model, truncated to `limit` chars."""
    s = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[: limit - 20] + "…(truncated)"


def clean_schema(schema: dict) -> dict:
    """Inline $refs and drop keys some providers (Gemini) reject, keeping the schema valid JSON schema."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return walk(dict(defs.get(ref, {})))
            out = {}
            for k, v in node.items():
                if k in ("$defs", "title", "examples", "additionalProperties", "default"):
                    continue
                if k == "anyOf":  # Optional[X] -> X (Gemini has limited anyOf support)
                    non_null = [x for x in v if x.get("type") != "null"]
                    if len(non_null) == 1:
                        out.update(walk(non_null[0]))
                        continue
                out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    out = walk(schema)
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out
