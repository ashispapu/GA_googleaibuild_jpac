"""Incident tools: get_incident (what is known about an incident: diagnosis, approved option, work order,
technician notes and the repair check). Used to answer questions about an incident and to re-analyse after a repair
that did not work."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from harness.events import clip
from tools.plant.presenter import presenter


def _summary(d, a) -> str:
    bits = [f"{d.get('incident_id')}: {str(d.get('stage', '')).replace('_', ' ')}"]
    if d.get("chosen_option"):
        bits.append(f"option {d['chosen_option']}")
    if d.get("verification"):
        bits.append(f"check {d['verification'].split(':')[0]}")
    return ", ".join(bits)


def _evidence(d, a, pol):
    notes = d.get("technician_notes") or []
    if not notes:
        return [], ""
    return [Evidence(kind=EvidenceKind.WORK_ORDER, ref_id=d.get("work_order_id") or d["incident_id"],
                     title="Technician notes", snippet=clip(" · ".join(notes[-3:]), 160),
                     link=f"/jobs/{d['incident_id']}")], ""


presenter("get_incident", label=lambda a: "Reading the incident record", summary=_summary, evidence=_evidence)
