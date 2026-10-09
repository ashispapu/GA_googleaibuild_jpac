from datetime import datetime
from enum import Enum
from typing import Any
from .common import Contract


class ActorType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AuditEntry(Contract):
    seq: int
    ts: datetime
    actor_type: ActorType
    actor: str
    action: str  # tool_call, approve, create_work_order, llm_call_redacted
    resource: str
    details: dict[str, Any]
    prev_hash: str
    hash: str


class AuditVerify(Contract):
    valid: bool
    entries_checked: int
    broken_at_seq: int | None = None
