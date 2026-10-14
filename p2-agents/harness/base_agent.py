"""BaseAgent: all the scaffolding every agent shares.

    result = await agent.run(ctx, task, tools=[...])

One run = STATUS started -> up to `max_steps` streamed LLM turns (reasoning + text stream live as *_delta events;
tool calls execute through the gateway with TOOL_CALL / TOOL_RESULT / EVIDENCE events) -> required data is fetched
if the model skipped it -> `finalize()` builds the typed RESULT in code (pipeline mode) -> STATUS finished.

A concrete agent lives in agents/<name>/ with its agent.py (a subclass), system_prompt.md, task templates and
agent.ini. Subclasses usually only override `finalize()` and, if the LLM should hand back structured wording,
set `output_models`.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from pydantic import BaseModel

from plantguard_contracts.schemas.incidents import EventType, Evidence
from plantguard_contracts.tools import TOOLS

from harness import prompts
from harness.config import AgentConfig, AgentsConfig, Policy
from harness.context import RunContext
from llm.types import LLMUnavailable, ToolCallRequest
from tools.base import Finish, Handoff, Tool, ToolInvocation, ToolOutcome

log = logging.getLogger("plantguard.agent")


def jsonable(v: Any) -> Any:
    """Blackboard values (dataclasses, datetimes, models) -> plain JSON-able data for prompts."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return jsonable(dataclasses.asdict(v))
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    return v


@dataclass
class Task:
    mode: str                     # pipeline | plan | summary | answer | sub | <custom>
    text: str                     # the user/task message for the model
    origin: str | None = None     # who gave the task (handoff origin / parent agent)


@dataclass
class FinalResult:
    result: Any                   # contract AgentResult (MonitorResult, Diagnosis, ...)
    message: str                  # short RESULT message for the UI
    confidence: float | None = None
    facts: dict[str, Any] = field(default_factory=dict)  # published on the blackboard for later agents


@dataclass
class AgentRunResult:
    agent: str
    mode: str
    text: str = ""
    structured: dict | None = None
    result: Any = None
    handoff: Handoff | None = None
    evidence: list[Evidence] = field(default_factory=list)
    tool_data: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    degraded: bool = False
    duration_ms: int = 0


@dataclass
class _Turn:
    text: str
    reasoning: str
    calls: list[ToolCallRequest]


def llm_reason(message: str) -> str:
    """Why the model is not used, in words a plant user understands (the full error stays in the server log)."""
    m = message.lower()
    paused = re.search(r"AI paused until ([^,;]+)", message)
    if "daily quota" in m:
        return "the Gemini key's daily quota is used up" + (f"; AI back {paused.group(1).strip()}" if paused else "")
    if "no module named 'google'" in m or 'no module named "google' in m:
        return "the Google sign-in package is missing (pip install google-auth)"
    if "no module named" in m:
        return "a Python package is missing (pip install -r requirements.txt)"
    if any(k in m for k in ("default credentials", "credentials were not found", "refresherror", "invalid_grant")):
        return "no Google sign-in for Vertex AI (run: gcloud auth application-default login)"
    if "aiplatform" in m and ("permission" in m or "403" in m or "disabled" in m):
        return "your Google account can't use Vertex AI in this project (enable the API / Vertex AI User role)"
    if any(k in m for k in ("api_key_service_blocked", "are blocked", "service_disabled", "has not been used",
                            "is disabled", "api key not valid", "api_key_invalid")):
        return "the API key isn't allowed to use the Gemini API (check the key's Google Cloud project)"
    if "no api key" in m:
        return "no Gemini API key is set"
    if "rate limit" in m:
        return "Gemini is rate-limiting requests right now"
    if "proxy" in m or "connection" in m or "timeout" in m or "timed out" in m:
        return "the network or proxy can't reach Gemini"
    if "request rejected" in m or "invalid" in m or "permission" in m or "401" in m or "403" in m:
        return "Gemini rejected the request (check the key and model name)"
    return "the AI model can't be reached"


class BaseAgent:
    # ---- knobs a subclass may change ----
    output_models: ClassVar[dict[str, type[BaseModel]]] = {}   # mode -> fields the LLM submits via submit_result
    result_modes: ClassVar[frozenset[str]] = frozenset({"pipeline"})   # modes that end with a typed RESULT
    thought_modes: ClassVar[frozenset[str]] = frozenset({"pipeline", "plan"})  # final text closes as THOUGHT
    handoff_modes: ClassVar[frozenset[str]] = frozenset({"pipeline", "plan", "summary", "answer"})  # never "sub"
    subagent_modes: ClassVar[frozenset[str]] = frozenset({"pipeline", "plan", "summary", "answer", "sub"})
    own_status_events: ClassVar[bool] = True

    def __init__(self, config: AgentConfig, app: AgentsConfig):
        self.config, self.app = config, app
        self.policy: Policy = config.policy

    # ------------------------------------------------------------------ prompts
    @property
    def name(self) -> str:
        return self.config.name

    def output_model_for(self, mode: str) -> type[BaseModel] | None:
        return self.output_models.get(mode)

    def template_vars(self, ctx: RunContext, task: Task | None = None) -> dict[str, Any]:
        inc = ctx.incident
        return {
            "agent": self.name, "principal": self.config.principal, "description": self.config.description,
            "mode": task.mode if task else "", "task": task.text if task else "", "origin": (task.origin if task else "") or "",
            "incident_id": inc.id, "asset_id": inc.asset_id, "line_id": inc.line_id, "plant_id": inc.plant_id,
            "trigger": inc.trigger.value, "severity": inc.severity.value, "title": inc.title,
            "question": getattr(inc, "question", None) or "", "now": ctx.now.isoformat(),
            "parent_id": getattr(inc, "parent_id", None) or "", "round": getattr(inc, "round", 1),
            "results": {k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else v) for k, v in ctx.results.items()},
            "answers": ctx.answers, "facts": jsonable({k: v for k, v in ctx.facts.items() if not k.startswith("_")}),
            "thought_max_chars": self.app.streaming.thought_max_chars,
            "policy": dict(self.policy),
        } | self.extra_vars(ctx)

    def extra_vars(self, ctx: RunContext) -> dict[str, Any]:
        return {}

    def system_prompt(self, ctx: RunContext, task: Task) -> str:
        return prompts.render(prompts.load(self.config.folder / self.config.prompt), self.template_vars(ctx, task))

    def build_task(self, ctx: RunContext, mode: str, text: str | None = None, origin: str | None = None) -> Task:
        """Task message from agents/<name>/<template for mode>; `text` (hand-off / sub-agent ask) fills {{task}}."""
        tpl = self.config.tasks.get(mode) or (self.config.tasks.get("default") if mode == "pipeline" else None)
        if tpl:
            v = self.template_vars(ctx, Task(mode, text or "", origin))
            return Task(mode, prompts.render(prompts.load(self.config.folder / tpl), v), origin)
        return Task(mode, text or f"Do your job for incident {ctx.incident.id}.", origin)

    # ------------------------------------------------------------------ main loop
    async def run(self, ctx: RunContext, task: Task, tools: Sequence[Tool], depth: int = 0) -> AgentRunResult:
        cfg = self.config
        principal, sub = cfg.principal, (cfg.name if cfg.is_helper else None)
        res = AgentRunResult(agent=cfg.name, mode=task.mode)
        t0 = time.monotonic()
        em = ctx.emitter
        if self.own_status_events:
            await em.emit(principal, EventType.STATUS, "started", sub_agent=sub)

        inv = ToolInvocation(ctx, cfg.name, principal, sub, depth)
        by_name = {t.name: t for t in tools}
        schemas = [t.schema() for t in tools]
        llm_cfg = self.app.llm_for(cfg.name)
        await self.prepare(ctx, task, inv, res)
        system = self.system_prompt(ctx, task)
        messages: list[dict] = [{"role": "user", "content": task.text}]
        final_text = ""

        for step in range(max(1, cfg.max_steps)):
            if ctx.degraded:
                res.degraded = True
                break
            res.steps += 1
            try:
                turn = await self._turn(ctx, system, messages, schemas, llm_cfg, task, principal, sub)
            except LLMUnavailable as e:
                await self._on_llm_failure(ctx, principal, e)
                res.degraded = True
                break
            if turn.text:
                final_text = turn.text
            if not turn.calls:
                break
            messages.append({"role": "assistant", "content": turn.text or None,
                             "tool_calls": [c.as_message_part() for c in turn.calls]})
            stop = False
            for call in turn.calls:
                if stop:
                    out = ToolOutcome(content="skipped: the task was already finished or handed off")
                else:
                    out = await self._call(ctx, by_name, call, inv, res)
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": out.content})
                if isinstance(out.control, Finish):
                    res.structured, stop = out.control.payload, True
                elif isinstance(out.control, Handoff):
                    res.handoff, stop = out.control, True
            if stop:
                break

        res.text = final_text
        if res.handoff:
            tpl = self.app.policy("handoff").get("reply_message", "This is now with {target}. Expect the answer from there.")
            await em.emit(principal, EventType.MESSAGE, tpl.format(target=res.handoff.target.replace("_", " ")),
                          sub_agent=sub)
        elif res.degraded and task.mode not in self.result_modes and not final_text:
            res.text = await self.degraded_answer(ctx, task, inv, res) or self.app.policy("handoff").get(
                "degraded_message", "The AI model isn't available ({reason}); the analysis continues with rules only."
            ).replace("{reason}", ctx.degraded_reason)
            await em.emit(principal, EventType.MESSAGE, res.text, sub_agent=sub)

        if task.mode in self.result_modes:
            await self.ensure_required(ctx, inv, res)
            final = await self.finalize(ctx, res)
            if final is not None:
                res.result = final.result
                ctx.results[cfg.name] = final.result
                ctx.facts.update(final.facts)
                await em.emit(principal, EventType.RESULT, final.message, result=final.result,
                              confidence=final.confidence, sub_agent=sub, duration_ms=int((time.monotonic() - t0) * 1000))
        if res.text:
            ctx.answers[cfg.name] = res.text
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        if self.own_status_events and task.mode not in self.result_modes:
            await em.emit(principal, EventType.STATUS, "finished", sub_agent=sub, duration_ms=res.duration_ms)
        return res

    async def _turn(self, ctx: RunContext, system: str, messages: list[dict], schemas: list[dict], llm_cfg,
                    task: Task, principal: str, sub: str | None) -> _Turn:
        """One streamed model call. Reasoning -> thought stream, text -> message stream, tool calls collected."""
        em = ctx.emitter
        thought, msg = em.stream(principal, "thought", sub), em.stream(principal, "message", sub)
        calls: list[ToolCallRequest] = []

        async def on_wait(seconds: float, reason: str, attempt: int, max_attempts: int) -> None:
            """Backoff / pacing wait: tell the UI (also keeps the live stream from going idle)."""
            if reason == "pacing":
                text = f"Waiting {seconds:.0f} s to stay within the AI service's request limit"
            else:
                text = f"AI service {reason}; trying again in {seconds:.0f} s (try {attempt} of {max_attempts})"
            await em.emit(principal, EventType.STATUS, text, sub_agent=sub)

        try:
            async for ch in ctx.llm.stream_chat(self.name, system, messages, schemas, llm_cfg, ctx.vault,
                                                on_wait=on_wait):
                if ch.kind == "reasoning":
                    await thought.push(ch.text)
                elif ch.kind == "text":
                    if thought.started and not thought.closed:
                        await thought.close(EventType.THOUGHT)
                    await msg.push(ch.text)
                elif ch.kind == "tool_call" and ch.tool_call is not None:
                    calls.append(ch.tool_call)
        except BaseException:
            await thought.close(None)
            await msg.close(None)
            raise
        if not thought.closed:
            await thought.close(EventType.THOUGHT if thought.started else None)
        text = msg.text.strip()
        if calls or task.mode in self.thought_modes:
            await msg.close(EventType.THOUGHT if text else None)
        else:
            await msg.close(EventType.MESSAGE if text else None)
        return _Turn(text, thought.text, calls)

    async def _call(self, ctx: RunContext, by_name: dict[str, Tool], call: ToolCallRequest, inv: ToolInvocation,
                    res: AgentRunResult) -> ToolOutcome:
        tool = by_name.get(call.name)
        if tool is None:
            return ToolOutcome(content=f"ERROR unknown tool '{call.name}'. Available: {', '.join(by_name)}",
                               is_error=True)
        if "__invalid_json__" in call.arguments:
            return ToolOutcome(content="ERROR arguments were not valid JSON; call again with a JSON object",
                               is_error=True)
        return await self.execute_tool(ctx, tool, dict(call.arguments), inv, res)

    async def execute_tool(self, ctx: RunContext, tool: Tool, args: dict, inv: ToolInvocation,
                           res: AgentRunResult) -> ToolOutcome:
        """Run one tool with its UI events. Plant tools: TOOL_CALL -> TOOL_RESULT -> EVIDENCE."""
        em = ctx.emitter
        if tool.kind != "plant":
            out = await tool.run(args, inv)
            res.evidence.extend(out.inherited_evidence)
            return out
        await em.emit(inv.principal, EventType.TOOL_CALL, tool.call_label(args), tool=tool.name, args=args,
                      sub_agent=inv.sub_agent)
        t0 = time.monotonic()
        out = await tool.run(args, inv)
        ms = int((time.monotonic() - t0) * 1000)
        if out.summary:
            await em.emit(inv.principal, EventType.TOOL_RESULT, out.summary, tool=tool.name, duration_ms=ms,
                          sub_agent=inv.sub_agent)
        if not out.is_error:
            res.tool_data[tool.name] = out.data
            fresh = self._fresh_evidence(ctx, out.evidence)
            if fresh:
                await em.emit(inv.principal, EventType.EVIDENCE, out.evidence_message or fresh[0].title,
                              evidence=fresh, sub_agent=inv.sub_agent)
            res.evidence.extend(out.evidence)
        return out

    @staticmethod
    def _fresh_evidence(ctx: RunContext, evidence: list[Evidence]) -> list[Evidence]:
        seen: set = ctx.facts.setdefault("_evidence_seen", set())
        fresh = [e for e in evidence if e.ref_id not in seen]
        seen.update(e.ref_id for e in fresh)
        return fresh

    async def _on_llm_failure(self, ctx: RunContext, principal: str, e: Exception) -> None:
        if self.app.runtime.on_llm_failure == "raise":
            raise e
        if not ctx.degraded:
            ctx.degraded = True
            msg = " ".join(str(e).split())
            ctx.degraded_reason = llm_reason(msg)
            if "AI paused until" in msg:
                log.info("%s: %s", principal, msg)  # already logged once when the pause started
            else:
                log.warning("LLM unavailable (%s); continuing rule-based", msg[:200])
            await ctx.emitter.emit(principal, EventType.ERROR,
                                   f"LLM_UNAVAILABLE: {ctx.degraded_reason}; continuing with rule-based analysis")

    # ------------------------------------------------------------------ data the code needs
    def default_args(self, ctx: RunContext, tool: str) -> dict | None:
        """Arguments for a required tool the model did not call. [policy] args.<tool> = {json} adds/overrides."""
        spec = TOOLS[tool]
        inc = ctx.incident
        known = {"asset_id": inc.asset_id, "line_id": inc.line_id, "plant_id": inc.plant_id,
                 "sku": ctx.facts.get("sku"), "supplier_id": ctx.facts.get("supplier_id")}
        args = {k: known[k] for k in spec.input.model_fields if known.get(k) is not None}
        extra = self.policy.get(f"args.{tool}")
        if extra:
            args.update(json.loads(prompts.render(extra, self.template_vars(ctx))))
        missing = [k for k, f in spec.input.model_fields.items() if f.is_required() and k not in args]
        return None if missing else args

    async def ensure_required(self, ctx: RunContext, inv: ToolInvocation, res: AgentRunResult) -> None:
        """Code guarantees the data the result needs: fetch every required tool the model skipped (reusing a
        teammate's identical call silently)."""
        for name in self.config.required_tools:
            if name in res.tool_data:
                continue
            args = self.default_args(ctx, name)
            if args is None:
                continue
            tool = ctx.toolbox.plant_tool(name)
            args = tool.normalise(args)
            cached = ctx.cached(name, args)
            if cached is not None:
                res.tool_data[name] = cached
                continue
            await self.execute_tool(ctx, tool, args, inv, res)

    def data(self, ctx: RunContext, res: AgentRunResult, tool: str) -> Any:
        """Tool data for this agent's result: its own call first, then anything fetched earlier in the run."""
        return res.tool_data.get(tool) if tool in res.tool_data else ctx.data(tool)

    def evidence_for(self, ctx: RunContext, res: AgentRunResult, tool: str, args: dict | None = None) -> list[Evidence]:
        d = self.data(ctx, res, tool)
        if d is None:
            return []
        evs, _ = ctx.toolbox.plant_tool(tool).presenter.evidence(d, args or {}, self.app.policy("evidence"))
        return evs

    # ------------------------------------------------------------------ override
    async def prepare(self, ctx: RunContext, task: Task, inv: ToolInvocation, res: AgentRunResult) -> None:
        """Runs after STATUS started and before the first LLM turn (e.g. compute numbers the prompt explains)."""
        return None

    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        """Build the typed contract result in code. Default: no typed result."""
        return None

    async def degraded_answer(self, ctx: RunContext, task: Task, inv: ToolInvocation, res: AgentRunResult) -> str | None:
        """Text to show when the model is unavailable (rule-based, from tool data). Default: the policy message."""
        return None


class Agent(BaseAgent):
    """Plain agent with no typed result (helpers, Q&A agents). Usable directly via `class = agent:Agent`."""
