"""Spare parts: where to get the part, how fast, at what cost. Sources and the recommendation are computed in code
from ERP data and [policy.planning] transfer times/costs; the LLM explains."""
from plantguard_contracts.schemas.common import Money
from plantguard_contracts.schemas.incidents import PartSource, SparePartsResult

from domain.planning import plan_part_sources, recommend_source
from harness.base_agent import AgentRunResult, BaseAgent, FinalResult
from harness.context import RunContext


class SparePartsAgent(BaseAgent):
    def default_args(self, ctx: RunContext, tool: str) -> dict | None:
        if tool == "get_supplier":
            part = ctx.data("check_stock")
            return {"supplier_id": part["supplier_id"]} if part else None
        if tool == "check_stock" and not ctx.facts.get("sku"):
            bom = ctx.data("get_bom")
            return {"sku": bom["items"][0]["sku"]} if bom and bom.get("items") else None
        return super().default_args(ctx, tool)

    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        part = self.data(ctx, res, "check_stock")
        if part is None:
            return None
        pol = self.app.policy("planning")
        local, sources = plan_part_sources(part, self.data(ctx, res, "get_supplier"), ctx.incident.plant_id, pol)
        rec = recommend_source(sources, ctx.facts.get("rul_hours"), pol)
        result = SparePartsResult(
            sku=part["sku"], local_stock=local, recommended_source=rec,
            sources=[PartSource(plant_id=s.plant_id, supplier_id=s.supplier_id, available_qty=s.available_qty,
                                eta_hours=s.eta_hours, cost=Money(amount=s.cost_eur)) for s in sources])
        best = sources[rec] if sources else None
        if best is None:
            msg = "No source found"
        elif best.mode == "local":
            msg = "Use local stock"
        elif best.plant_id:
            msg = f"Use Plant {best.plant_id.split('-')[-1]} stock"
        else:
            msg = "Order from the supplier"
        return FinalResult(result, msg, facts={"part_sources": sources, "recommended_source": rec, "sku": part["sku"],
                                               "unit_cost": float(part["unit_cost"]["amount"]),
                                               "supplier_id": part.get("supplier_id")})
