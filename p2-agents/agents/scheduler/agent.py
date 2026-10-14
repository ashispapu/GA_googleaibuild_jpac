"""Scheduler: finds the repair window. Window, technician, speed and the deadline check are computed in code
(domain.planning) from the order book, the schedule, technicians and [policy.planning]."""
from plantguard_contracts.schemas.incidents import RepairWindow, SchedulerResult

from domain.planning import pick_order, plan_repair_window, production_plan, required_skill
from harness.base_agent import AgentRunResult, BaseAgent, FinalResult
from harness.context import RunContext


class SchedulerAgent(BaseAgent):
    def skill(self, ctx: RunContext) -> str:
        return ctx.facts.get("required_skill") or required_skill(ctx.facts.get("sku"), ctx.facts.get("failure_mode"),
                                                                 self.app.policy("planning"))

    def extra_vars(self, ctx: RunContext) -> dict:
        return {"required_skill": self.skill(ctx)}

    async def ensure_required(self, ctx: RunContext, inv, res: AgentRunResult) -> None:
        """If the model looked for the wrong skill, also fetch the people who have the one this repair needs."""
        await super().ensure_required(ctx, inv, res)
        skill = self.skill(ctx)
        techs = (self.data(ctx, res, "find_technicians") or {}).get("items", [])
        if techs is not None and not any(skill in t.get("skills", []) for t in techs):
            tool = ctx.toolbox.plant_tool("find_technicians")
            await self.execute_tool(ctx, tool, tool.normalise({"skill": skill}), inv, res)

    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        orders = (self.data(ctx, res, "get_orders") or {}).get("items", [])
        order = pick_order(orders, ctx.incident.line_id)
        if order is None:
            return None
        pol = self.app.policy("planning")
        prod = production_plan(order, pol)
        sources = ctx.facts.get("part_sources") or []
        rec = ctx.facts.get("recommended_source", 0)
        eta = sources[rec].eta_hours if sources else 0.0
        techs = (self.data(ctx, res, "find_technicians") or {}).get("items", [])
        skill = self.skill(ctx)
        w = plan_repair_window(ctx.now, eta, prod, techs, float(ctx.facts.get("risk_72h", 0.0)), pol, skill)
        result = SchedulerResult(order_id=prod.order_id, windows=[RepairWindow(
            start=w.start, end=w.end, shift=w.shift, technician_ids=w.technician_ids,
            line_speed_before_pct=w.speed_before_pct, deadline_met=w.deadline_met, lost_output_units=w.lost_output_units)])
        msg = f"{w.shift.capitalize()} shift window found" + ("" if w.deadline_met else " (deadline at risk)")
        schedule = (self.data(ctx, res, "get_schedule") or {}).get("items", [])
        return FinalResult(result, msg, facts={"production": prod, "window": w, "schedule": schedule,
                                               "due": prod.due})
