from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union
from pydantic import Field
from .common import (AssetId, Contract, IncidentId, LineId, Money, OptionId, PlantId,
                     Probability, Sku, TechnicianId)
from .erp import StockTransferDraft
from .maintenance import WorkOrderDraft


class IncidentStatus(str, Enum):
    OPEN = "open"
    ANALYSING = "analysing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"   # actions created in ERP / maintenance
    RESOLVED = "resolved"   # technician feedback received


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


PlannedAction = Annotated[Union[WorkOrderAction, StockTransferAction, SpeedChangeAction],
                          Field(discriminator="kind")]


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
