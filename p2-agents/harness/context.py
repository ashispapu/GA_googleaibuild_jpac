"""RunContext: everything one agent run shares - incident, config, emitter, LLM client, tool gateway, the blackboard
(facts + typed results of earlier agents) and a log of every tool result. One context per incident run."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

from plantguard_contracts.schemas.incidents import Incident

from guard.redactor import RedactionVault
from harness.config import AgentsConfig
from harness.events import EventEmitter
from harness.memory import CaseMemory

if TYPE_CHECKING:  # pragma: no cover
    from harness.base_agent import AgentRunResult
    from llm.client import LLMClient
    from tools.registry import Toolbox


class ToolExecutor(Protocol):
    """The backend ToolGateway (in-process) or agents.tool_client.HttpToolClient - same call signature (C4)."""
    async def call(self, agent: str, tool: str, args: dict, incident_id: str) -> dict: ...


@dataclass
class ToolRecord:
    principal: str
    tool: str
    args: dict
    data: Any
    ms: int


def _key(tool: str, args: dict) -> str:
    return tool + ":" + json.dumps(args, sort_keys=True, default=str)


@dataclass
class RunContext:
    incident: Incident
    config: AgentsConfig
    emitter: EventEmitter
    llm: "LLMClient"
    gateway: ToolExecutor
    toolbox: "Toolbox"
    memory: CaseMemory
    runner: Callable[..., Awaitable["AgentRunResult"]]  # (name, mode, task_text, origin, depth) -> result
    vault: RedactionVault = field(default_factory=RedactionVault)
    facts: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)   # agent name -> typed contract result
    answers: dict[str, str] = field(default_factory=dict)   # agent name -> last free-text answer
    tool_log: list[ToolRecord] = field(default_factory=list)
    handoffs: int = 0
    degraded: bool = False
    degraded_reason: str = "the AI model can't be reached"  # plain words, shown to the user
    _cache: dict[str, Any] = field(default_factory=dict)

    # ---- tools ----
    async def call_tool(self, principal: str, tool: str, args: dict) -> Any:
        """Always through the gateway (audit + allow-list); the result is kept on the blackboard."""
        t0 = time.monotonic()
        data = await self.gateway.call(principal, tool, args, self.incident.id)
        self._cache[_key(tool, args)] = data
        self.tool_log.append(ToolRecord(principal, tool, args, data, int((time.monotonic() - t0) * 1000)))
        return data

    def cached(self, tool: str, args: dict) -> Any | None:
        return self._cache.get(_key(tool, args))

    def data(self, tool: str) -> Any | None:
        """Latest result of `tool` in this run, whoever fetched it."""
        for rec in reversed(self.tool_log):
            if rec.tool == tool:
                return rec.data
        return None

    def called(self, tool: str) -> bool:
        return any(r.tool == tool for r in self.tool_log)

    # ---- agents ----
    async def run_agent(self, name: str, mode: str = "sub", task_text: str | None = None, origin: str | None = None,
                        depth: int = 0) -> "AgentRunResult":
        return await self.runner(name, mode, task_text, origin, depth)

    # ---- time ----
    @property
    def now(self) -> datetime:
        """Reference time for planning: the data's own clock ([policy.planning] reference_time = data|wallclock)."""
        if "now" in self.facts:
            return self.facts["now"]
        return self.emitter._clock() if self.config.policy("planning").get("reference_time") == "wallclock" \
            else self.incident.created_at
