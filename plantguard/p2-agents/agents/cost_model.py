"""Deterministic cost model for the Decision agent (AG-7). The LLM never does the maths.
Must reproduce fixtures/story/options_INC-0042.json: OPT-A 22,385 · OPT-B 2,485 · OPT-C 33,485."""
DOWNTIME_EUR_PER_H = 2000
LATE_PENALTY_EUR = 5000
PART_EUR = 85
TRANSFER_EUR = {"express": 900, "standard": 350}


def option_cost(downtime_h: float, transfer: str | None, labour: float, slowdown_eur: float, late: bool) -> dict:
    d = downtime_h * DOWNTIME_EUR_PER_H + slowdown_eur
    l = TRANSFER_EUR.get(transfer, 0) if transfer else 0
    p = LATE_PENALTY_EUR if late else 0
    return {"downtime": d, "parts": PART_EUR, "logistics": l, "labour": labour, "late_penalty": p,
            "total": d + PART_EUR + l + labour + p}
