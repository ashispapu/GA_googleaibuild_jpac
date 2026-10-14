"""IncidentContext from an Incident alone (no repair workflow data): used by the mock server and by the backend in
fixtures mode for the get_incident tool."""
from .schemas.incidents import Incident, IncidentStatus
from .schemas.workflow import IncidentContext, Stage

_STAGE = {IncidentStatus.OPEN: Stage.AI_ANALYSING, IncidentStatus.ANALYSING: Stage.AI_ANALYSING,
          IncidentStatus.AWAITING_APPROVAL: Stage.AWAITING_APPROVAL, IncidentStatus.APPROVED: Stage.WAITING_TECHNICIAN,
          IncidentStatus.EXECUTED: Stage.WAITING_TECHNICIAN, IncidentStatus.IN_REPAIR: Stage.TECHNICIAN_WORKING,
          IncidentStatus.VERIFYING: Stage.VERIFYING, IncidentStatus.REPAIR_FAILED: Stage.REPAIR_FAILED,
          IncidentStatus.REJECTED: Stage.REJECTED, IncidentStatus.RESOLVED: Stage.RESOLVED}


def basic_context(inc: Incident) -> IncidentContext:
    d = inc.diagnosis
    return IncidentContext(incident_id=inc.id, asset_id=inc.asset_id, status=inc.status, stage=_STAGE[inc.status],
                           round=inc.round, diagnosis=d.root_cause if d else None, failure_mode=d.failure_mode if d else None,
                           confidence=d.confidence if d else None, chosen_option=inc.selected_option_id,
                           approved_by=inc.approved_by, technician_id=inc.assigned_technician_id,
                           work_order_id=inc.work_order_id, excluded_failure_modes=inc.excluded_failure_modes)
