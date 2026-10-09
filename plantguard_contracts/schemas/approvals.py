from datetime import datetime
from enum import Enum
from .common import Contract, IncidentId, OptionId, Role, TechnicianId, UserId


class DecisionKind(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRequest(Contract):
    option_id: OptionId
    decision: DecisionKind
    reason: str


class ActionResult(Contract):
    kind: str  # work_order | stock_transfer | speed_change
    ref_id: str | None = None  # WO-2026-0153, STO-4500012
    ok: bool
    message: str


class Approval(Contract):
    id: str
    incident_id: IncidentId
    option_id: OptionId
    decision: DecisionKind
    reason: str
    approver_id: UserId
    approver_role: Role
    approval_token: str | None  # signed JWT, None when rejected
    approved_at: datetime
    executed_actions: list[ActionResult]  # backend executes on approve


class FeedbackRequest(Contract):
    technician_id: TechnicianId
    confirmed: bool
    actual_root_cause: str
    notes: str | None = None


class Feedback(Contract):
    id: str
    incident_id: IncidentId
    confirmed: bool
    actual_root_cause: str
    stored_for_retraining: bool
    new_similar_case_confidence: float | None = None  # shown in scene 8
    created_at: datetime
