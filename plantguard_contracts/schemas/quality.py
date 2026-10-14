from datetime import datetime
from .common import Contract, TimeWindow


class Defect(Contract):
    id: str
    line_id: str
    asset_id: str | None = None
    defect_type: str  # e.g. bore_diameter
    measured: float
    spec_min: float
    spec_max: float
    ts: datetime


class DefectSummary(Contract):
    line_id: str
    window: TimeWindow
    defect_rate_pct: float
    trend_pct: float  # change vs previous window
    items: list[Defect]


class Inspection(Contract):
    id: str
    line_id: str
    lot_id: str
    passed: bool
    ts: datetime
