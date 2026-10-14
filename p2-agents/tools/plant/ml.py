"""ML tools: get_asset_health."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from tools.plant.presenter import pct, presenter


def _evidence(d, args, pol):
    drivers = [f["description"] for f in d.get("top_features", [])[: pol.int("ml_top_drivers", 2)]]
    ev = Evidence(kind=EvidenceKind.ML, ref_id=f"{d['asset_id']}:{d.get('model_version', 'model')}",
                  title=f"Failure risk {pct(d['risk_72h'])} in 72 h",
                  snippet=("Top drivers: " + ", ".join(drivers)) if drivers else "",
                  link=f"/assets/{d['asset_id']}")
    return [ev], f"ML model: {pct(d['risk_72h'])} risk in 72 h"


presenter(
    "get_asset_health",
    label=lambda a: "Checking asset health",
    summary=lambda d, a: (f"Risk {pct(d['risk_72h'])} in 72 h, RUL ~{round(d['rul_hours'])} h, "
                          f"anomaly {d.get('anomaly_score', 0):.2f}"),
    evidence=_evidence,
    compact=lambda d: {k: d.get(k) for k in ("asset_id", "health", "risk_72h", "rul_hours", "anomaly_score",
                                            "predicted_failure_mode", "rule_threshold_breached", "model_version",
                                            "evaluated_at")}
    | {"top_features": [f["description"] for f in d.get("top_features", [])]},
)
