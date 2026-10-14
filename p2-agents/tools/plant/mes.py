"""MES tools: get_orders, get_schedule."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from tools.plant.presenter import presenter


def _orders_evidence(d, a, pol):
    line = a.get("line_id")
    mine = [o for o in d.get("items", []) if not line or o["line_id"] == line]
    if not mine:
        return [], ""
    o = min(mine, key=lambda o: o["due_at"])
    return [Evidence(kind=EvidenceKind.SCHEDULE, ref_id=o["id"], title=f"{o['id']} due {o['due_at'][:10]}",
                     snippet=f"{o['quantity'] - o['completed']:,} of {o['quantity']:,} {o['product']} left",
                     link=f"/orders/{o['id']}")], ""


presenter(
    "get_orders",
    label=lambda a: "Reading order book",
    summary=lambda d, a: "; ".join(f"{o['id']} {o['completed']}/{o['quantity']} due {o['due_at'][:10]}"
                                   for o in d.get("items", []) if not a.get("line_id") or o["line_id"] == a["line_id"]),
    evidence=_orders_evidence,
)
presenter(
    "get_schedule",
    label=lambda a: "Reading the production schedule",
    summary=lambda d, a: f"{len(d.get('items', []))} planned slots",
)
