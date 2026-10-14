"""Decision: builds the costed options from every strategy in [option.*], ranks them and decides whether a human
must review. All maths is deterministic (domain.planning + agents.cost_model) so the demo shows the same numbers
every time (C3); the LLM writes the explanation THOUGHT."""
from __future__ import annotations

from pydantic import TypeAdapter

from plantguard_contracts.schemas.incidents import Option, OptionsResponse

from agents.cost_model import CostModel
from domain.planning import PlanningFacts, build_option, mode_word, pick_order, production_plan, rank_options
from harness.base_agent import AgentRunResult, BaseAgent, FinalResult, Task
from harness.context import RunContext
from tools.base import ToolInvocation

_options = TypeAdapter(list[Option])


class DecisionAgent(BaseAgent):
    def facts(self, ctx: RunContext, res: AgentRunResult) -> PlanningFacts | None:
        f, inc = ctx.facts, ctx.incident
        prod = f.get("production")
        if prod is None:
            order = pick_order((self.data(ctx, res, "get_orders") or {}).get("items", []), inc.line_id)
            if order is None:
                return None
            prod = production_plan(order, self.app.policy("planning"))
        health = self.data(ctx, res, "get_asset_health") or {}
        pol = self.app.policy("planning")
        mode = f.get("failure_mode") or health.get("predicted_failure_mode") or "unknown"
        return PlanningFacts(
            incident_id=inc.id, asset_id=inc.asset_id, plant_id=inc.plant_id, line_id=inc.line_id, now=ctx.now,
            prod=prod, sku=f.get("sku") or "", part_name=f.get("part_name") or "part",
            unit_cost=float(f.get("unit_cost", 0.0)), root_cause=f.get("root_cause") or "",
            risk_72h=float(f.get("risk_72h", health.get("risk_72h", 0.0))),
            diagnosis_confidence=float(f.get("diagnosis_confidence", 0.0)),
            sources=f.get("part_sources") or [], window=f.get("window"), schedule=f.get("schedule") or [],
            energy=f.get("energy") or {}, similar_work_order=f.get("similar_work_order"),
            failure_mode=mode, repair_noun=mode_word(pol, "repair_noun", mode, "Repair"),
            risk_noun=mode_word(pol, "risk_noun", mode, "the machine"),
            repair_verb=mode_word(pol, "repair_verb", mode, "Replace"),
            repair_word=mode_word(pol, "repair_word", mode, "repair"))

    _final: FinalResult | None = None

    async def prepare(self, ctx: RunContext, task: Task, inv: ToolInvocation, res: AgentRunResult) -> None:
        """Compute the options first, so the model explains real numbers (they go into {{options}})."""
        if task.mode in self.result_modes:
            self._final = self.compute(ctx, res)

    def extra_vars(self, ctx: RunContext) -> dict:
        if not self._final:
            return {"options": "not computed"}
        r = self._final.result
        return {"options": [{"id": o.id, "label": o.label, "total_eur": o.cost.total.amount, "deadline_met": o.deadline_met,
                             "downtime_h": o.downtime_hours, "failure_risk": o.failure_risk_until_repair,
                             "energy_kwh": o.energy_kwh, "confidence": o.confidence, "recommended": o.recommended,
                             "risks": o.risks} for o in r.options],
                "needs_human_review": r.needs_human_review, "review_reason": r.reason}

    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        return self._final or self.compute(ctx, res)

    def compute(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        pf = self.facts(ctx, res)
        if pf is None:
            return None
        pol = self.app.policy("planning")
        cm = CostModel(pol)
        plans = [build_option(oid, opt, pf, pol, cm) for oid, opt in self.app.options.items()]
        rank_options(plans)
        options = _options.validate_python([{
            "id": o.id, "label": o.label, "summary": o.summary, "actions": o.actions,
            "cost": {k: {"amount": v, "currency": "EUR"} for k, v in o.cost.items()},
            "downtime_hours": o.downtime_hours, "deadline_met": o.deadline_met, "energy_kwh": o.energy_kwh,
            "co2_kg": o.co2_kg, "failure_risk_until_repair": o.failure_risk_until_repair,
            "confidence": o.confidence, "risks": o.risks, "recommended": o.recommended} for o in plans])
        threshold = pol.float("review_threshold", 0.7)
        writes = any(a.kind in ("work_order", "stock_transfer", "purchase_order") for o in options for a in o.actions)
        low = pf.diagnosis_confidence < threshold
        reasons = []
        if low:
            reasons.append(f"Diagnosis confidence {pf.diagnosis_confidence:.2f} is below {threshold:.2f}")
        if writes:
            reasons.append("the plan creates work orders")
        reason = (" and ".join(reasons) + ".") if reasons else "Every plan is approved by a person."
        reason = reason[0].upper() + reason[1:]
        result = OptionsResponse(incident_id=ctx.incident.id, options=options, needs_human_review=low or writes
                                 or pol.bool("always_review", True), reason=reason)
        best = next((o for o in options if o.recommended), None)
        msg = f"{len(options)} options, {best.id.split('-')[-1]} recommended" if best else f"{len(options)} options"
        return FinalResult(result, msg, confidence=pf.diagnosis_confidence)
