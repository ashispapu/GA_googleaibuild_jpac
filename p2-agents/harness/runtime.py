"""PlantAgentRuntime - implements plantguard_contracts.interfaces.AgentRuntime (C3).

run(incident) streams AgentEvents while the agents work:
  orchestrator STATUS started -> the steps of [flow.<trigger>] in order (hand-offs are inserted right after the
  agent that diverted) -> orchestrator STATUS finished.
The flow, agents, tools, models and limits all come from config/agents.ini.

Failure policy ([runtime] on_llm_failure):
  raise   -> the exception propagates; the backend's FallbackAgentRuntime emits LLM_UNAVAILABLE and replays the
             recorded run (C3 default).
  degrade -> one ERROR event, then every agent finishes from tool data with rule-based results.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from typing import AsyncIterator

from plantguard_contracts.schemas.approvals import Approval, Feedback
from plantguard_contracts.schemas.incidents import Agent, AgentEvent, EventType, Incident

from guard.redactor import RedactionVault
from harness.base_agent import AgentRunResult
from harness.config import AgentsConfig, FlowStep
from harness.context import RunContext, ToolExecutor
from harness.events import EventEmitter, utcnow
from harness.memory import Case, CaseMemory
from harness.registry import AgentRegistry
from llm.client import LLMClient
from tools.registry import Toolbox

log = logging.getLogger("plantguard.runtime")
ORCH = Agent.ORCHESTRATOR.value
_DONE = object()


class PlantAgentRuntime:  # implements interfaces.AgentRuntime
    def __init__(self, config: AgentsConfig, gateway: ToolExecutor, llm: LLMClient, *, clock=utcnow,
                 memory: CaseMemory | None = None, registry: AgentRegistry | None = None,
                 toolbox: Toolbox | None = None):
        self.config, self.gateway, self.llm, self.clock = config, gateway, llm, clock
        self.memory = memory or CaseMemory()
        self.registry = registry or AgentRegistry(config)
        self.toolbox = toolbox or Toolbox(config)
        self.last_context: RunContext | None = None  # for tests / debugging

    # ------------------------------------------------------------------ v1.2: options chat
    async def refine_options(self, incident, options, history: list[dict], message: str, author: str) -> dict:
        """An approver's suggestion -> {"reply", "option"} (agents/decision/refine.py). Numbers come from code."""
        from agents.decision.refine import OptionRefiner
        return await OptionRefiner(self.config, self.gateway, self.llm, self.clock).refine(
            incident, options, history, message, author)

    def looks_like_plan(self, message: str) -> bool:
        """Does this text ask for a different repair plan (timing, speed, part source, technician)?"""
        from agents.decision.refine import parse_rules
        return not parse_rules(message, self.clock(), {}).empty()

    # ------------------------------------------------------------------ AgentRuntime
    async def run(self, incident: Incident) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue = asyncio.Queue()

        async def sink(ev: AgentEvent) -> None:
            await queue.put(ev)

        vault = RedactionVault()
        emitter = EventEmitter(incident.id, self.config.streaming, sink, self.clock, vault)
        ctx = RunContext(incident=incident, config=self.config, emitter=emitter, llm=self.llm, gateway=self.gateway,
                         toolbox=self.toolbox, memory=self.memory, runner=None, vault=vault)  # type: ignore[arg-type]
        ctx.runner = lambda name, mode, text, origin, depth: self._run_agent(ctx, name, mode, text, origin, depth)
        self.last_context = ctx

        async def drive():
            try:
                await asyncio.wait_for(self._drive(ctx), self.config.runtime.max_run_seconds)
            finally:
                await queue.put(_DONE)

        task = asyncio.create_task(drive())
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                yield item
            await task  # re-raise LLMUnavailable / timeout / bugs for the backend fallback
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

    async def on_feedback(self, incident: Incident, feedback: Feedback) -> None:
        mode = incident.diagnosis.failure_mode if incident.diagnosis else None
        self.memory.record(Case(incident.id, incident.asset_id, mode, feedback.confirmed,
                                feedback.new_similar_case_confidence, feedback.actual_root_cause))

    async def on_approval(self, incident: Incident, approval: Approval) -> None:
        log.info("incident %s: option %s %s by %s", incident.id, approval.option_id, approval.decision.value,
                 approval.approver_id)

    # ------------------------------------------------------------------ flow
    async def _drive(self, ctx: RunContext) -> None:
        em = ctx.emitter
        await em.emit(ORCH, EventType.STATUS, "started")
        steps: deque[tuple[FlowStep, str | None, str | None]] = deque(
            (s, None, None) for s in self.config.flow(ctx.incident.trigger.value))
        handoff_mode = self.config.policy("handoff").get("target_mode", "answer")
        while steps:
            step, text, origin = steps.popleft()
            res = await self._run_agent(ctx, step.agent, step.mode, text, origin, 0)
            if res.handoff:
                steps.appendleft((FlowStep(res.handoff.target, handoff_mode), res.handoff.task, res.agent))
        await em.emit(ORCH, EventType.STATUS, "finished")

    async def _run_agent(self, ctx: RunContext, name: str, mode: str, text: str | None, origin: str | None,
                         depth: int) -> AgentRunResult:
        agent = self.registry.create(name)
        task = agent.build_task(ctx, mode, text, origin)
        tools = self.toolbox.for_agent(agent, mode)  # tools are passed in, never looked up by the agent
        return await agent.run(ctx, task, tools, depth=depth)
