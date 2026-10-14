from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union
from pydantic import Field
from .common import (AssetId, Contract, IncidentId, LineId, Money, OptionId, PlantId,
                     Probability, Sku, TechnicianId, UserId)
from .erp import PurchaseOrderDraft, StockTransferDraft
from .maintenance import WorkOrderDraft


class IncidentStatus(str, Enum):
    OPEN = "open"
    ANALYSING = "analysing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"   # actions created in ERP / maintenance (work order waiting for the technician)
    RESOLVED = "resolved"   # technician feedback received
    # ---- additive (v1.2): the repair itself ----
    IN_REPAIR = "in_repair"          # assigned technician started the work order (machine stopped for maintenance)
    VERIFYING = "verifying"          # repair reported done; the machine is watched before the technician can close
    REPAIR_FAILED = "repair_failed"  # readings still abnormal after the repair; technician can ask or re-analyse
    AWAITING_SIGNOFF = "awaiting_signoff"  # an option was approved; more signatures needed before anything runs


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Agent(str, Enum):
    ORCHESTRATOR = "orchestrator"
    MONITOR = "monitor"
    DIAGNOSIS = "diagnosis"
    SPARE_PARTS = "spare_parts"
    SCHEDULER = "scheduler"
    ENERGY = "energy"
    DECISION = "decision"


class EventType(str, Enum):
    STATUS = "status"            # agent started / finished; message = "started" | "finished"
    THOUGHT = "thought"          # one reasoning step, plain language, <= 200 chars
    TOOL_CALL = "tool_call"      # tool + args set
    TOOL_RESULT = "tool_result"  # tool set, message = short summary of result
    EVIDENCE = "evidence"        # evidence[] set
    RESULT = "result"            # agent's final typed result in `result`
    ERROR = "error"
    # ---- additive (v1.1): live streaming + agent hand-offs ----
    THOUGHT_DELTA = "thought_delta"  # chunk of the model's live reasoning; append by stream_id
    MESSAGE_DELTA = "message_delta"  # chunk of the model's live answer text; append by stream_id
    MESSAGE = "message"              # full answer text of an agent (closes the message_delta stream with the same stream_id)
    HANDOFF = "handoff"              # task diverted to another agent; args = {"to": <agent>, "task": <text>}


class EvidenceKind(str, Enum):
    MANUAL = "manual"
    WORK_ORDER = "work_order"
    SENSOR = "sensor"
    ML = "ml"
    QUALITY = "quality"
    INVENTORY = "inventory"
    ENERGY = "energy"
    SCHEDULE = "schedule"


class Evidence(Contract):
    kind: EvidenceKind
    ref_id: str  # WO-2024-0871 | MAN-SPINDLE-200#7.4 | EQ-3012:vibration_rms
    title: str
    snippet: str
    link: str | None = None  # UI route, e.g. /assets/EQ-3012?signal=vibration_rms


class IncidentTrigger(str, Enum):
    ML_ALERT = "ml_alert"
    ALARM = "alarm"
    USER_QUESTION = "user_question"


class IncidentCreate(Contract):
    asset_id: AssetId
    trigger: IncidentTrigger
    alarm_id: str | None = None
    question: str | None = None
    parent_id: IncidentId | None = None  # additive (v1.2): a question about this incident (its context is given to the agents)


# ---------- Agent results (payload of the RESULT event per agent) ----------

class MonitorResult(Contract):
    asset_id: AssetId
    severity: Severity
    summary: str
    risk_72h: Probability
    rul_hours: float


class Diagnosis(Contract):
    root_cause: str
    failure_mode: str  # bearing_wear | overheating | misalignment | unknown
    confidence: Probability  # < 0.7 forces human review
    evidence: list[Evidence]
    alternatives: list[str] = []
    required_parts: list[Sku] = []


class PartSource(Contract):
    plant_id: PlantId | None = None  # None = supplier
    supplier_id: str | None = None
    available_qty: int
    eta_hours: float
    cost: Money


class SparePartsResult(Contract):
    sku: Sku
    local_stock: int
    sources: list[PartSource]  # sorted fastest first
    recommended_source: int  # index into sources


class RepairWindow(Contract):
    start: datetime
    end: datetime
    shift: str  # morning | evening | night
    technician_ids: list[TechnicianId]
    line_speed_before_pct: int  # speed to run until repair
    deadline_met: bool
    lost_output_units: int


class SchedulerResult(Contract):
    order_id: str
    windows: list[RepairWindow]


class EnergyEstimate(Contract):
    option_id: OptionId
    energy_kwh: float
    energy_cost: Money
    co2_kg: float
    note: str


class EnergyResult(Contract):
    estimates: list[EnergyEstimate]
    excess_power_pct: float  # EQ-3012 draw vs baseline, wear indicator


# ---------- Options and actions ----------

class WorkOrderAction(Contract):
    kind: Literal["work_order"] = "work_order"
    description: str
    draft: WorkOrderDraft


class StockTransferAction(Contract):
    kind: Literal["stock_transfer"] = "stock_transfer"
    description: str
    draft: StockTransferDraft


class SpeedChangeAction(Contract):
    kind: Literal["speed_change"] = "speed_change"
    description: str
    line_id: LineId
    speed_pct: int = Field(ge=0, le=100)
    start: datetime
    end: datetime | None = None


class PurchaseOrderAction(Contract):
    """additive (v1.2): order the part from the supplier."""
    kind: Literal["purchase_order"] = "purchase_order"
    description: str
    draft: PurchaseOrderDraft


PlannedAction = Annotated[Union[WorkOrderAction, StockTransferAction, SpeedChangeAction, PurchaseOrderAction],
                          Field(discriminator="kind")]


class ApprovalStep(Contract):
    """additive (v1.2): one signature an option needs before its actions run (see C7 approval chain)."""
    key: str                  # requesting_plant | source_plant:PLANT-B | manager
    label: str                # "Maintenance lead, Plant A" / "Release by Plant B" / "Plant manager co-sign"
    plant_id: PlantId
    roles: list[str]          # roles that may sign this step
    reason: str               # why the step is needed, plain words
    decision: Literal["pending", "approved", "declined"] = "pending"
    signed_by: UserId | None = None
    signed_name: str | None = None
    signed_at: datetime | None = None
    note: str | None = None   # the signer's reason


class SignoffState(Contract):
    """additive (v1.2): the approval chain of the option being signed (or the last one declined)."""
    option_id: OptionId
    status: Literal["in_progress", "declined", "complete"]
    steps: list[ApprovalStep]
    started_by: UserId
    started_at: datetime


class CostBreakdown(Contract):
    downtime: Money
    parts: Money
    logistics: Money
    labour: Money
    late_penalty: Money
    total: Money


class Option(Contract):
    id: OptionId
    label: str
    summary: str
    actions: list[PlannedAction]
    cost: CostBreakdown
    downtime_hours: float
    deadline_met: bool
    energy_kwh: float
    co2_kg: float
    failure_risk_until_repair: Probability
    confidence: Probability
    risks: list[str]
    recommended: bool
    # ---- additive (v1.2) ----
    approvals_needed: list[ApprovalStep] = []   # filled by the backend approval policy
    origin: Literal["ai", "approver"] = "ai"     # "approver": built from an approver's suggestion in the options chat
    suggested_by: str | None = None             # name of that approver


class OptionsResponse(Contract):
    incident_id: IncidentId
    options: list[Option]
    needs_human_review: bool
    reason: str  # why review is needed, e.g. "Diagnosis confidence 0.60 < 0.70"


AgentResult = Union[MonitorResult, Diagnosis, SparePartsResult, SchedulerResult,
                    EnergyResult, OptionsResponse]


class Incident(Contract):
    id: IncidentId
    title: str
    plant_id: PlantId
    line_id: LineId
    asset_id: AssetId
    trigger: IncidentTrigger
    severity: Severity
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime
    diagnosis: Diagnosis | None = None
    selected_option_id: OptionId | None = None
    question: str | None = None  # additive (v1.1): the user's question for trigger=user_question
    # ---- additive (v1.2): life cycle links ----
    parent_id: IncidentId | None = None          # question thread: the incident it is about
    round: int = 1                               # analysis round (2+ after a repair that did not work)
    excluded_failure_modes: list[str] = []       # causes already repaired without success (agents skip them)
    previous_findings: str | None = None         # what the technician found in the previous round
    assigned_technician_id: TechnicianId | None = None
    work_order_id: str | None = None
    approved_by: UserId | None = None
    opened_by: str | None = None                 # USR-... for questions / manual incidents, system otherwise
    resolved_at: datetime | None = None
    signoff: SignoffState | None = None          # approval chain in progress / last declined


class AgentEvent(Contract):
    """Streamed on WS /incidents/{id}/stream and bus channel incident.{id}.events, ordered by seq."""
    incident_id: IncidentId
    seq: int = Field(ge=1)
    ts: datetime
    agent: Agent
    type: EventType
    message: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    evidence: list[Evidence] = []
    confidence: Probability | None = None
    result: AgentResult | None = None
    duration_ms: int | None = None
    # ---- additive (v1.1) ----
    stream_id: str | None = None  # groups *_delta chunks; the closing THOUGHT/MESSAGE carries the same id
    sub_agent: str | None = None  # set when a helper sub-agent works on behalf of `agent`
    round: int | None = None  # additive (v1.2): analysis round this event belongs to
