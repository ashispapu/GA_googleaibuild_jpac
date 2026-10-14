"""ask_<agent>: run another agent as a SUB-AGENT. The sub-agent does the work and its answer comes back to the caller,
which keeps control and continues (contrast with handoff, where control leaves the caller)."""
from __future__ import annotations

import json

from tools.base import Tool, ToolInvocation, ToolOutcome


class SubAgentTool(Tool):
    kind = "agent"

    def __init__(self, target: str, description: str):
        self.target = target
        self.name = f"ask_{target}"
        self.description = (f"Ask the '{target}' sub-agent to do a focused task and return its findings to you. "
                            f"It works for you and reports back. Its job: {description or target}.")

    def parameters(self) -> dict:
        return {"type": "object", "properties": {
            "task": {"type": "string", "description": "What the sub-agent should find out, in one or two sentences."}},
            "required": ["task"]}

    def call_label(self, args: dict) -> str:
        return f"Asking {self.target.replace('_', ' ')}"

    async def run(self, args: dict, inv: ToolInvocation) -> ToolOutcome:
        task = str(args.get("task") or "").strip()
        if not task:
            return ToolOutcome(content="ERROR: 'task' is required", is_error=True)
        if inv.depth + 1 > inv.ctx.config.runtime.max_subagent_depth:
            return ToolOutcome(content="ERROR: sub-agent depth limit reached; answer with what you have", is_error=True)
        res = await inv.ctx.run_agent(self.target, mode="sub", task_text=task, origin=inv.agent, depth=inv.depth + 1)
        payload = {"agent": self.target, "answer": res.text, "findings": res.structured,
                   "evidence": [f"{e.title}: {e.snippet}" for e in res.evidence]}
        return ToolOutcome(content=json.dumps(payload, ensure_ascii=False, default=str), data=res,
                           inherited_evidence=list(res.evidence))
