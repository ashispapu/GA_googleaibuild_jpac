from datetime import datetime
from enum import Enum
from pydantic import Field
from .common import AssetId, Contract, IncidentId, Sku, TechnicianId, WorkOrderId


class WorkOrderType(str, Enum):
    CORRECTIVE = "corrective"
    PREVENTIVE = "preventive"
    PREDICTIVE = "predictive"


class WorkOrderStatus(str, Enum):
    CREATED = "created"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class PartUse(Contract):
    sku: Sku
    quantity: int = Field(ge=1)


class WorkOrderDraft(Contract):
    """What agents propose. Becomes a WorkOrderCreate once approved."""
    asset_id: AssetId
    type: WorkOrderType
    title: str
    root_cause: str
    parts: list[PartUse]
    technician_id: TechnicianId
    planned_start: datetime
    duration_hours: float = Field(gt=0)
    incident_id: IncidentId


class WorkOrderCreate(WorkOrderDraft):
    approval_token: str


class WorkOrder(Contract):
    id: WorkOrderId
    asset_id: AssetId
    type: WorkOrderType
    title: str
    root_cause: str | None = None
    findings: str | None = None  # technician notes after the job
    parts: list[PartUse] = []
    technician_id: TechnicianId | None = None
    planned_start: datetime | None = None
    duration_hours: float | None = None
    status: WorkOrderStatus
    incident_id: IncidentId | None = None
    created_at: datetime
