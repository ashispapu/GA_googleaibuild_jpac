"""Energy: kWh / € / CO2 of every option strategy ([option.*] in agents.ini), computed in code from line energy,
tariffs and the grid CO2 factor."""
from plantguard_contracts.schemas.common import Money
from plantguard_contracts.schemas.incidents import EnergyEstimate, EnergyResult

from domain.planning import estimate_energy, excess_power_pct
from harness.base_agent import AgentRunResult, BaseAgent, FinalResult
from harness.context import RunContext


class EnergyAgent(BaseAgent):
    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        energy = self.data(ctx, res, "get_energy")
        if energy is None:
            return None
        tariffs = (self.data(ctx, res, "get_tariffs") or {}).get("items", [])
        co2 = float((self.data(ctx, res, "get_co2_factor") or {}).get("kg_per_kwh", 0.0))
        asset = next((a for a in energy.get("by_asset", []) if a["asset_id"] == ctx.incident.asset_id), None)
        if asset is None:
            return None
        pol = self.app.policy("planning")
        due = ctx.facts.get("due") or ctx.now
        window = ctx.facts.get("window")
        plans = {oid: estimate_energy(oid, opt, asset, tariffs, co2, ctx.now, due, window, pol)
                 for oid, opt in self.app.options.items()}
        result = EnergyResult(
            excess_power_pct=round(excess_power_pct(asset), pol.int("excess_pct_decimals", 0)),
            estimates=[EnergyEstimate(option_id=p.option_id, energy_kwh=p.kwh, energy_cost=Money(amount=p.cost_eur),
                                      co2_kg=p.co2_kg, note=p.note) for p in plans.values()])
        best = min(plans.values(), key=lambda p: p.kwh, default=None)
        msg = (f"Option {best.option_id.split('-')[-1]} saves ~{abs(best.kwh):.0f} kWh" if best and best.kwh < 0
               else "No energy saving between options")
        return FinalResult(result, msg, facts={"energy": plans})
