"""handoff_task: DIVERT the task to another agent. The caller stops, says the task is now with the other agent, and
the runtime runs the target next; the answer comes from the target, not back to the caller."""
from __future__ import annotations

from plantguard_contracts.schemas.incidents import EventType

from tools.base import Handoff, Tool, ToolInvocation, ToolOutcome


class HandoffTool(Tool):
    kind = "agent"
    name = "handoff_task"

    def __init__(self, targets: dict[str, str]):
        self.targets = targets  # name -> what it does
        roster = "; ".join(f"{k}: {v}" for k, v in targets.items())
        self.description = ("Hand this task over to another agent that is better placed to answer. You stop working "
                            f"on it and the user gets the answer from that agent. Agents: {roster}.")

    def parameters(self) -> dict:
        return {"type": "object", "properties": {
            "agent": {"type": "string", "enum": sorted(self.targets), "description": "Who takes over."},
            "task": {"type": "string", "description": "The task, rewritten so the other agent can act on it alone."},
            "reason": {"type": "string", "description": "One short sentence: why that agent."}},
            "required": ["agent", "task"]}

    async def run(self, args: dict, inv: ToolInvocation) -> ToolOutcome:
        ctx = inv.ctx
        target, task = str(args.get("agent") or ""), str(args.get("task") or "").strip()
        if target not in self.targets:
            return ToolOutcome(content=f"ERROR: unknown agent '{target}'. Choose one of {sorted(self.targets)}",
                               is_error=True)
        if not task:
            return ToolOutcome(content="ERROR: 'task' is required", is_error=True)
        if ctx.handoffs >= ctx.config.runtime.max_handoffs:
            return ToolOutcome(content="ERROR: hand-off limit reached for this run; answer yourself", is_error=True)
        ctx.handoffs += 1
        reason = str(args.get("reason") or "").strip()
        tpl = ctx.config.policy("handoff").get("event_message", "Handed to {target}: {task}")
        await ctx.emitter.emit(inv.principal, EventType.HANDOFF, tpl.format(target=target.replace("_", " "), task=task),
                               args={"to": target, "task": task, **({"reason": reason} if reason else {})},
                               sub_agent=inv.sub_agent)
        return ToolOutcome(content=f"Handed over to {target}. Stop now.",
                           control=Handoff(target=target, task=task, reason=reason, origin=inv.agent))
