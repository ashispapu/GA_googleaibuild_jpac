"""Deterministic cost model for the Decision agent (AG-7). The LLM never does the maths.
Rates come from config/agents.ini [policy.planning]; with the shipped values it reproduces
fixtures/story/options_INC-0042.json: OPT-A 22,385 · OPT-B 2,485 · OPT-C 33,485."""
from __future__ import annotations

# Legacy constants (kept for anything that imported them from the starter).
DOWNTIME_EUR_PER_H = 2000
LATE_PENALTY_EUR = 5000
PART_EUR = 85
TRANSFER_EUR = {"express": 900, "standard": 350}


def option_cost(downtime_h: float, transfer: str | None, labour: float, slowdown_eur: float, late: bool) -> dict:
    """Starter API, unchanged."""
    d = downtime_h * DOWNTIME_EUR_PER_H + slowdown_eur
    l = TRANSFER_EUR.get(transfer, 0) if transfer else 0
    p = LATE_PENALTY_EUR if late else 0
    return {"downtime": d, "parts": PART_EUR, "logistics": l, "labour": labour, "late_penalty": p,
            "total": d + PART_EUR + l + labour + p}


class CostModel:
    """Cost of one option, every rate from the policy:
    downtime_eur_per_h / downtime_eur_per_h_night, <mode>_transfer_eur, labour_eur, night_premium_eur, late_penalty_eur.
    """

    def __init__(self, policy):
        self.p = policy

    @property
    def late_penalty(self) -> float:
        return self.p.float("late_penalty_eur")

    def option(self, *, downtime_h: float, night: bool, transfer: str | None, night_labour: bool, late: bool,
               unit_cost: float, slowdown_eur: float = 0.0) -> dict[str, float]:
        rate = self.p.float("downtime_eur_per_h_night") if night else self.p.float("downtime_eur_per_h")
        downtime = downtime_h * rate + slowdown_eur
        if transfer == "supplier":
            logistics = self.p.float("supplier_shipping_eur", 0)
        elif transfer == "supplier_express":
            logistics = self.p.float("supplier_shipping_eur", 0) + self.p.float("supplier_express_eur", 0)
        elif transfer and transfer != "local":
            logistics = self.p.float(f"{transfer}_transfer_eur", 0)
        else:
            logistics = 0.0
        labour = self.p.float("labour_eur") + (self.p.float("night_premium_eur") if night_labour else 0.0)
        penalty = self.late_penalty if late else 0.0
        parts = unit_cost
        return {"downtime": round(downtime, 2), "parts": round(parts, 2), "logistics": round(logistics, 2),
                "labour": round(labour, 2), "late_penalty": round(penalty, 2),
                "total": round(downtime + parts + logistics + labour + penalty, 2)}
