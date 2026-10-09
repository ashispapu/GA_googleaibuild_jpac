"""Shared conventions. All timestamps are UTC ISO-8601, money is EUR, units are SI unless noted."""
from datetime import datetime
from enum import Enum
from typing import Annotated, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Contract(BaseModel):
    """Base for all contracts: unknown fields are rejected so drift fails loudly."""
    model_config = ConfigDict(extra="forbid")


# ---- ID formats (every team generates and parses IDs the same way) ----
PlantId = Annotated[str, Field(pattern=r"^PLANT-[A-Z]$", examples=["PLANT-A"])]
LineId = Annotated[str, Field(pattern=r"^L\d+$", examples=["L3"])]
StationId = Annotated[str, Field(pattern=r"^L\d+-ST\d+$", examples=["L3-ST2"])]
AssetId = Annotated[str, Field(pattern=r"^EQ-\d{4}$", examples=["EQ-3012"])]
Sku = Annotated[str, Field(pattern=r"^[A-Z0-9]+-[A-Z0-9-]+$", examples=["SKF-6205"])]
OrderId = Annotated[str, Field(pattern=r"^ORD-\d{4}$", examples=["ORD-5521"])]
WorkOrderId = Annotated[str, Field(pattern=r"^WO-\d{4}-\d{4}$", examples=["WO-2024-0871"])]
IncidentId = Annotated[str, Field(pattern=r"^INC-\d{4}$", examples=["INC-0042"])]
OptionId = Annotated[str, Field(pattern=r"^OPT-[A-Z]$", examples=["OPT-B"])]
TechnicianId = Annotated[str, Field(pattern=r"^TECH-\d{3}$", examples=["TECH-104"])]
UserId = Annotated[str, Field(pattern=r"^USR-[a-z]+$", examples=["USR-lead"])]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class Role(str, Enum):
    OPERATOR = "operator"
    MAINTENANCE_LEAD = "maintenance_lead"
    PLANT_MANAGER = "plant_manager"
    ADMIN = "admin"


class Health(str, Enum):
    OK = "ok"            # risk_72h < 0.3
    WARNING = "warning"  # 0.3 <= risk_72h < 0.7
    CRITICAL = "critical"  # risk_72h >= 0.7 or active critical alarm


class MachineState(str, Enum):
    RUN = "run"
    IDLE = "idle"
    FAULT = "fault"
    STOPPED = "stopped"


class DataQuality(str, Enum):
    GOOD = "good"
    STALE = "stale"            # no update for > 5 s
    OUT_OF_RANGE = "out_of_range"
    MISSING = "missing"


class Money(Contract):
    amount: float
    currency: str = "EUR"


class TimeWindow(Contract):
    start: datetime
    end: datetime


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    next_cursor: str | None = None


class ErrorCode(str, Enum):
    UNAUTHENTICATED = "UNAUTHENTICATED"      # 401 missing/expired token
    FORBIDDEN = "FORBIDDEN"                  # 403 role lacks permission
    PLANT_SCOPE = "PLANT_SCOPE"              # 403 resource in another plant
    NOT_FOUND = "NOT_FOUND"                  # 404
    VALIDATION = "VALIDATION"                # 422
    CONFLICT = "CONFLICT"                    # 409 e.g. incident already approved
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"  # 403 write without approval_token
    APPROVAL_INVALID = "APPROVAL_INVALID"    # 403 token expired / wrong incident / wrong action
    RATE_LIMITED = "RATE_LIMITED"            # 429
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"      # 503 agents fall back to replay
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"      # 501


class ErrorResponse(Contract):
    code: ErrorCode
    message: str
    trace_id: str
    details: dict | None = None
