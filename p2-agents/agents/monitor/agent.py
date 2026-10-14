"""Monitor: confirms the signal. RESULT = MonitorResult (severity from risk bands in code, wording from the LLM)."""
import math

from pydantic import BaseModel, Field

from plantguard_contracts.schemas.incidents import MonitorResult, Severity

from domain.timeline import to_dt
from harness.base_agent import AgentRunResult, BaseAgent, FinalResult
from harness.context import RunContext


class MonitorDraft(BaseModel):
    summary: str = Field(description="One plain sentence, max 120 chars: what the sensors show and how soon it may "
                                     "fail. Use only numbers from the tool results.", max_length=160)


class MonitorAgent(BaseAgent):
    output_models = {"pipeline": MonitorDraft}

    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        h = self.data(ctx, res, "get_asset_health")
        if h is None:
            return None
        p = self.policy
        risk, rul = float(h["risk_72h"]), float(h["rul_hours"])
        sev = Severity.HIGH if risk >= p.float("high_risk", 0.7) else (
            Severity.MEDIUM if risk >= p.float("medium_risk", 0.3) else Severity.LOW)
        summary = ((res.structured or {}).get("summary") or "").strip()
        if not summary:
            top = (h.get("top_features") or [{}])[0].get("description", "Risk rising")
            summary = f"{top}, failure likely within {max(1, math.ceil(rul / 24))} days"
        facts = {"risk_72h": risk, "rul_hours": rul, "ml_failure_mode": h.get("predicted_failure_mode")}
        if self.app.policy("planning").get("reference_time", "data") == "data" and h.get("evaluated_at"):
            facts["now"] = to_dt(h["evaluated_at"])
        return FinalResult(MonitorResult(asset_id=h["asset_id"], severity=sev, summary=summary[:160], risk_72h=risk,
                                         rul_hours=rul), f"{sev.value.capitalize()} severity", facts=facts)
