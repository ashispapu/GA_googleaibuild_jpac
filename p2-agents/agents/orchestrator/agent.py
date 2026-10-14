"""Coordinator. Opens the run with a plan (THOUGHT), closes it with a summary for the Ask panel (MESSAGE), and in
the user_question flow answers the question itself - asking teammates as sub-agents or handing the question off.
Without a model it still answers questions from the data (incident record or machine health)."""
from harness.base_agent import AgentRunResult, BaseAgent, Task
from harness.context import RunContext
from tools.base import ToolInvocation


class OrchestratorAgent(BaseAgent):
    own_status_events = False                       # the runtime emits orchestrator started / finished (C3)
    result_modes = frozenset()                      # the orchestrator has no typed RESULT
    thought_modes = frozenset({"plan"})             # plan -> THOUGHT; summary / answer -> MESSAGE
    subagent_modes = frozenset({"answer"})          # only consults teammates when answering a question
    handoff_modes = frozenset({"answer"})
    plant_tool_modes = frozenset({"answer"})        # get_incident / get_asset_health only when answering a question

    def extra_vars(self, ctx: RunContext) -> dict:
        parent = getattr(ctx.incident, "parent_id", None)
        return {"parent_note": f" (about incident {parent}; call get_incident with incident_id {parent})" if parent else ""}

    async def degraded_answer(self, ctx: RunContext, task: Task, inv: ToolInvocation, res: AgentRunResult) -> str | None:
        if task.mode != "answer":
            return None
        inc = ctx.incident
        note = f"(The AI model isn't available: {ctx.degraded_reason}. This answer comes straight from the plant data.) "
        parent = getattr(inc, "parent_id", None)
        if parent and "get_incident" in ctx.toolbox.plant:
            tool = ctx.toolbox.plant_tool("get_incident")
            await self.execute_tool(ctx, tool, {"incident_id": parent}, inv, res)
            d = res.tool_data.get("get_incident") or {}
            if d:
                parts = [f"Incident {parent} on {d.get('asset_id')} is at stage: {str(d.get('stage', '')).replace('_', ' ')}."]
                if d.get("diagnosis"):
                    conf = d.get("confidence")
                    parts.append(f"Diagnosis: {d['diagnosis']}" + (f" ({round(conf * 100)}% sure)." if conf is not None else "."))
                if d.get("chosen_option"):
                    parts.append(f"Approved plan: {d['chosen_option']}" + (f", approved by {d['approved_by']}." if d.get("approved_by") else "."))
                if d.get("work_order_id"):
                    parts.append(f"Work order {d['work_order_id']} ({d.get('work_order_status')}), parts: "
                                 f"{', '.join(d.get('parts') or []) or 'none'}.")
                if d.get("verification"):
                    parts.append(f"Repair check: {d['verification']}.")
                if d.get("technician_notes"):
                    parts.append(f"Latest note: {d['technician_notes'][-1]}")
                if d.get("excluded_failure_modes"):
                    parts.append("Already ruled out: " + ", ".join(m.replace('_', ' ') for m in d["excluded_failure_modes"]) + ".")
                return note + " ".join(parts)
        if "get_asset_health" in ctx.toolbox.plant:
            tool = ctx.toolbox.plant_tool("get_asset_health")
            await self.execute_tool(ctx, tool, {"asset_id": inc.asset_id}, inv, res)
            h = res.tool_data.get("get_asset_health") or {}
            if h:
                drivers = "; ".join(f["description"] for f in (h.get("top_features") or [])[:2])
                mode = (h.get("predicted_failure_mode") or "no clear fault").replace("_", " ")
                return (note + f"{inc.asset_id}: failure risk {round(h['risk_72h'] * 100)}% in 72 h, likely cause: {mode}. "
                        f"Main signals: {drivers}.")
        return None
