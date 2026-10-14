"""Contract v1.2 (additive): the repair life cycle, technician jobs, incident lists, sessions, documents, the
PlantGuard Garden activity feed and simulator status."""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .common import Contract, IncidentId, OptionId, PlantId, Role, TechnicianId, UserId
from .erp import TransferStatus
from .incidents import ApprovalStep, Diagnosis, Incident, IncidentStatus, Option, OptionsResponse
from .maintenance import PartUse, WorkOrder


class Stage(str, Enum):
    """Where an incident is, in plain terms (drives the tags in every incident list)."""
    AI_ANALYSING = "ai_analysing"            # agents are working on it
    AWAITING_APPROVAL = "awaiting_approval"  # options ready, a lead/manager must approve one
    AWAITING_SIGNOFF = "awaiting_signoff"    # approved by one person; other plant / manager still to sign
    WAITING_TECHNICIAN = "waiting_technician"  # approved; work order waits for the assigned technician
    TECHNICIAN_WORKING = "technician_working"
    VERIFYING = "verifying"                  # repair done, machine being watched
    READY_TO_CLOSE = "ready_to_close"        # check passed, technician must confirm and close
    REPAIR_FAILED = "repair_failed"          # check failed
    REJECTED = "rejected"
    RESOLVED = "resolved"
    ANSWERED = "answered"                    # question threads


class PersonRef(Contract):
    id: str
    name: str


class VerificationView(Contract):
    result: Literal["pending", "passed", "failed"]
    started_at: datetime
    ends_at: datetime
    details: str | None = None
    max_risk: float | None = None


class IncidentOverview(Contract):
    """One row of GET /incidents: the incident plus everything linked to it."""
    incident: Incident
    asset_name: str
    line_name: str
    stage: Stage
    status_tag: Literal["in_progress", "done"]
    ai_working: bool
    approval: PersonRef | None = None
    approved_at: datetime | None = None
    option_id: OptionId | None = None
    option_label: str | None = None
    technician: PersonRef | None = None
    work_order_id: str | None = None
    work_order_status: str | None = None
    planned_start: datetime | None = None
    verification: VerificationView | None = None
    machine_state: str | None = None
    questions: int = 0
    resolution: str | None = None


class WorkNoteRequest(Contract):
    note: str


class WorkStartRequest(Contract):
    note: str | None = None


class WorkCompleteRequest(Contract):
    findings: str
    parts_used: list[PartUse] | None = None  # default: the work order's parts


class ResolveRequest(Contract):
    confirmed: bool  # did the repair confirm the AI's diagnosis?
    actual_root_cause: str
    notes: str | None = None


class ReanalyseRequest(Contract):
    reason: str


class WorkUpdate(Contract):
    ts: datetime
    author: PersonRef | None = None
    kind: str  # created | started | note | parts | completed | verification | reanalysis | closed
    note: str


class PartNeed(Contract):
    sku: str
    description: str
    quantity: int
    in_stock: int  # at the plant doing the repair
    transfer_id: str | None = None
    transfer_status: TransferStatus | None = None
    transfer_eta: datetime | None = None
    ready: bool
    purchase_order_id: str | None = None          # additive: part ordered from the supplier
    purchase_order_status: str | None = None
    purchase_order_eta: datetime | None = None


class ManualRef(Contract):
    doc_id: str
    title: str
    section: str
    heading: str
    page: int
    why: str  # e.g. "Troubleshooting for bearing wear"


class JobView(Contract):
    """GET /incidents/{id}/job: everything the technician needs for the repair."""
    incident: Incident
    overview: IncidentOverview
    diagnosis: Diagnosis | None = None
    option: Option | None = None
    approval_reason: str | None = None
    work_order: WorkOrder | None = None
    updates: list[WorkUpdate]
    parts: list[PartNeed]
    manuals: list[ManualRef]
    can_start: bool
    blocked_reason: str | None = None
    can_complete: bool
    can_close: bool
    can_reanalyse: bool
    is_assignee: bool


class MaintenanceLogEntry(Contract):
    """A closed incident in the maintenance log ("done")."""
    incident_id: IncidentId
    asset_id: str
    asset_name: str
    plant_id: PlantId
    line_id: str
    opened_at: datetime
    resolved_at: datetime
    rounds: int
    ai_root_cause: str | None
    actual_root_cause: str | None
    ai_confirmed: bool | None
    solution: str
    parts: list[PartUse]
    work_orders: list[str]
    technician: PersonRef | None
    approved_by: PersonRef | None
    option_id: OptionId | None
    downtime_hours: float | None
    time_to_fix_hours: float


class SessionInfo(Contract):
    id: str
    user_id: UserId
    user_name: str
    role: Role
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None
    current: bool = False
    active: bool = True


class DocumentInfo(Contract):
    id: str
    kind: str  # manual | work_order
    title: str
    asset_type: str | None = None
    equipment_model: str | None = None
    asset_id: str | None = None
    sections: int


class DocumentSection(Contract):
    section: str
    heading: str
    page: int
    text: str


class DocumentDetail(Contract):
    id: str
    kind: str
    title: str
    asset_type: str | None = None
    equipment_model: str | None = None
    asset_id: str | None = None
    sections: list[DocumentSection]
    used_by: list[str]


class GardenAgent(Contract):
    """Where one incident's agent is in the system right now (PlantGuard Garden)."""
    incident_id: IncidentId
    asset_id: str
    plant_id: PlantId
    agent: str           # orchestrator | monitor | ... | executor | technician | lead
    node: str            # system node id, e.g. erp, cmms, vdb, llm, historian
    activity: str        # plain words: "Spare parts · checking stock of SKF-6205"
    stage: Stage
    ts: datetime
    color: int           # stable colour index per incident


class GardenSnapshot(Contract):
    now: datetime
    agents: list[GardenAgent]


class SimStatus(Contract):
    mode: Literal["sim", "fixtures"]
    now: datetime
    scale: float
    random_faults: bool
    developing_faults: int
    open_incidents: int


class FaultRequest(Contract):
    asset_id: str | None = None
    failure_mode: str | None = None  # bearing_wear | misalignment | overheating


class FaultResponse(Contract):
    asset_id: str
    message: str


class SimSpeedRequest(Contract):
    scale: float  # 0 pauses the plant


class IncidentContext(Contract):
    """get_incident tool output: what the agents may know about an incident (no secrets, no ground truth)."""
    incident_id: IncidentId
    asset_id: str
    status: IncidentStatus
    stage: Stage
    round: int
    diagnosis: str | None = None
    failure_mode: str | None = None
    confidence: float | None = None
    chosen_option: str | None = None
    approved_by: str | None = None
    technician_id: TechnicianId | None = None
    work_order_id: str | None = None
    work_order_status: str | None = None
    parts: list[str] = []
    technician_notes: list[str] = []
    verification: str | None = None
    excluded_failure_modes: list[str] = []


# ---- additive (v1.2): approvers discuss the options with the AI; each suggestion can add an option ----
class OptionChatMessage(Contract):
    seq: int
    role: Literal["user", "assistant"]
    author_id: UserId | None = None
    author_name: str | None = None
    text: str
    option_id: OptionId | None = None   # option added by this answer
    ts: datetime


class OptionChatRequest(Contract):
    message: str = Field(min_length=2, max_length=1000)
    # true: a message from another chat (Ask panel / assistant). Only act when it asks for a different plan;
    # anything else is left alone and nothing is stored.
    only_if_plan: bool = False


class OptionChatResponse(Contract):
    reply: OptionChatMessage
    added_option_id: OptionId | None = None
    options: OptionsResponse


class SignoffTask(Contract):
    """An approval step waiting for this user (GET /incidents/signoffs)."""
    incident_id: IncidentId
    asset_id: str
    plant_id: PlantId
    option_id: OptionId
    option_label: str
    step: ApprovalStep
    requested_by: str
    requested_at: datetime
