from datetime import datetime
from enum import Enum
from .common import Contract, TimeWindow


class OrderStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    AT_RISK = "at_risk"
    COMPLETED = "completed"


class ProductionOrder(Contract):
    id: str  # ORD-5521
    product: str
    quantity: int
    completed: int
    due_at: datetime
    line_id: str
    status: OrderStatus


class ScheduleSlot(Contract):
    line_id: str
    start: datetime
    end: datetime
    order_id: str | None = None
    planned_speed_pct: int = 100
    kind: str = "production"  # production | maintenance | idle


class LineOEE(Contract):
    line_id: str
    window: TimeWindow
    availability: float
    performance: float
    quality: float
    oee: float
    downtime_minutes: int
