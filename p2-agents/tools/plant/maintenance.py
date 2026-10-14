"""Maintenance tools: get_maintenance_history."""
from datetime import datetime

from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from harness.events import clip
from tools.plant.presenter import presenter


def _when(v) -> str:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).strftime("%b %Y")
    except (TypeError, ValueError):
        return "the past"


def _history_evidence(d, args, pol):
    failures = [w for w in d.get("items", []) if w.get("type") == "corrective" and w.get("root_cause")]
    evs = [Evidence(kind=EvidenceKind.WORK_ORDER, ref_id=w["id"], title=f"Same kind of failure in {_when(w.get('planned_start'))}",
                    snippet=clip(f"{w['root_cause']}. {w.get('findings') or ''}", 140), link=f"/work-orders/{w['id']}")
           for w in failures[: pol.int("history_max_evidence", 1)]]
    return evs, (f"Past failure on this machine: {failures[0]['id']}" if failures else "")


presenter(
    "get_maintenance_history",
    label=lambda a: "Checking past failures",
    summary=lambda d, a: f"{len(d.get('items', []))} past work orders",
    evidence=_history_evidence,
    compact=lambda d: {"items": [{k: w.get(k) for k in ("id", "type", "title", "root_cause", "findings", "parts",
                                                         "planned_start", "duration_hours", "status")}
                                 for w in d.get("items", [])]},
)
