"""ERP tools: check_stock, get_supplier."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from tools.plant.presenter import presenter


def _plant(pid: str) -> str:
    return f"Plant {pid.split('-')[-1]}"


def _stock_summary(d, a):
    parts = [f"{_plant(s['plant_id'])}: {s['quantity']}" for s in d.get("stock", [])]
    return ". ".join(parts + [f"Supplier: {d.get('supplier_lead_time_days')} days"]) + "."


def _stock_evidence(d, a, pol):
    title = ", ".join(f"{s['quantity']} in {_plant(s['plant_id'])}" for s in d.get("stock", []))
    return [Evidence(kind=EvidenceKind.INVENTORY, ref_id=d["sku"], title=title,
                     snippet=f"Supplier lead time {d.get('supplier_lead_time_days')} days",
                     link=f"/parts/{d['sku']}")], ""


presenter("check_stock", label=lambda a: "Checking stock", summary=_stock_summary, evidence=_stock_evidence)
presenter("get_supplier", label=lambda a: "Checking the supplier",
          summary=lambda d, a: f"{d.get('name')}: {d.get('lead_time_days')} days"
                               + (", express possible" if d.get("express_available") else ", no express"))
