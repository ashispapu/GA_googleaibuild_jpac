"""Python interfaces between work packages. Implement these exactly; depend only on these.

Owner legend: [BE] Backend core, [AG] Agents & RAG, [ML] ML & Data, [FE] Frontend.
"""
from datetime import datetime
from typing import AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel

from .schemas.approvals import Approval, Feedback
from .schemas.audit import AuditEntry
from .schemas.energy import Co2Factor, LineConsumption, Tariff
from .schemas.engineering import AssetBom, DocSearchRequest, DocSearchResponse
from .schemas.erp import Part, StockTransfer, StockTransferCreate, Supplier
from .schemas.incidents import AgentEvent, Incident
from .schemas.maintenance import WorkOrder, WorkOrderCreate
from .schemas.mes import LineOEE, ProductionOrder, ScheduleSlot
from .schemas.ml import AssetHealth, FeatureWindow, ModelInfo
from .schemas.ot import Alarm, Signal, SignalSeries, TelemetryMessage
from .schemas.plants import Plant
from .schemas.quality import DefectSummary
from .schemas.security import RedactionReport
from .schemas.workforce import Shift, Technician


# ---------------- Data connectors [BE] ----------------
# Mock implementation reads /fixtures. A real system (SAP, OPC-UA...) swaps in later.
# All methods raise KeyError for unknown IDs; routers turn that into 404 NOT_FOUND.

class PlantConnector(Protocol):
    def list_plants(self) -> list[Plant]: ...


class OTConnector(Protocol):
    def get_signals(self, asset_id: str, signals: list[Signal], minutes: int) -> list[SignalSeries]: ...
    def get_alarms(self, plant_id: str | None, active: bool) -> list[Alarm]: ...
    def subscribe(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]: ...


class ERPConnector(Protocol):
    def get_part(self, sku: str) -> Part: ...
    def get_supplier(self, supplier_id: str) -> Supplier: ...
    def create_stock_transfer(self, req: StockTransferCreate) -> StockTransfer: ...


class MESConnector(Protocol):
    def get_orders(self, line_id: str | None) -> list[ProductionOrder]: ...
    def get_schedule(self, line_id: str, hours: int) -> list[ScheduleSlot]: ...
    def get_oee(self, line_id: str) -> LineOEE: ...
    def set_line_speed(self, line_id: str, speed_pct: int, start: datetime, end: datetime | None) -> None: ...


class MaintenanceConnector(Protocol):
    def get_history(self, asset_id: str) -> list[WorkOrder]: ...
    def create_work_order(self, req: WorkOrderCreate) -> WorkOrder: ...


class QualityConnector(Protocol):
    def get_defects(self, line_id: str, hours: int) -> DefectSummary: ...


class EngineeringConnector(Protocol):
    def get_bom(self, asset_id: str) -> AssetBom: ...


class WorkforceConnector(Protocol):
    def get_shifts(self, plant_id: str) -> list[Shift]: ...
    def find_technicians(self, skill: str | None, on_shift: bool | None) -> list[Technician]: ...


class EnergyConnector(Protocol):
    def get_consumption(self, line_id: str, hours: int) -> LineConsumption: ...
    def get_tariffs(self) -> list[Tariff]: ...
    def get_co2_factor(self) -> Co2Factor: ...


# ---------------- ML [ML] ----------------

@runtime_checkable
class FeatureExtractor(Protocol):
    """Turns a 10-min buffer of telemetry for ONE asset into the FEATURES dict (schemas.ml.FEATURES)."""
    def extract(self, asset_id: str, buffer: list[TelemetryMessage],
                hours_since_service: float) -> FeatureWindow: ...


@runtime_checkable
class PredictiveModel(Protocol):
    """Loaded once by the backend: ml.serving.load_model('models/latest') -> PredictiveModel.
    Must answer in < 50 ms. Must be deterministic for the same input (stable demo)."""
    version: str
    def score(self, window: FeatureWindow) -> AssetHealth: ...
    def info(self) -> list[ModelInfo]: ...


# ---------------- RAG + LLM guard [AG] ----------------

class Retriever(Protocol):
    """Hybrid search over manuals and past work orders. Every chunk carries doc_id + section."""
    def search(self, req: DocSearchRequest) -> DocSearchResponse: ...


class Redactor(Protocol):
    """Called on EVERY prompt before it leaves the backend. Returns cleaned text + report."""
    def redact(self, text: str, agent: str) -> tuple[str, RedactionReport]: ...
    def restore(self, text: str, report: RedactionReport) -> str: ...  # put placeholders back for the UI


# ---------------- Platform [BE] ----------------

class EventBus(Protocol):
    """Redis pub/sub in prod, in-memory in tests. Channels: schemas.bus.CHANNELS."""
    async def publish(self, channel: str, message: BaseModel) -> None: ...
    def subscribe(self, channel: str) -> AsyncIterator[dict]: ...


class AuditLogger(Protocol):
    """Append-only, hash-chained: hash = sha256(prev_hash + canonical_json(entry_without_hash))."""
    def log(self, actor_type: str, actor: str, action: str, resource: str, details: dict) -> AuditEntry: ...
    def verify(self) -> tuple[bool, int, int | None]: ...


class ToolGateway(Protocol):
    """[BE] provides, [AG] consumes. Enforces tools.TOOLS allow-list, approval tokens, audit, timeouts.
    Agents NEVER call connectors directly."""
    async def call(self, agent: str, tool: str, args: dict, incident_id: str) -> dict: ...


# ---------------- Agent runtime [AG] ----------------

class AgentRuntime(Protocol):
    """Backend calls run() when an incident opens and forwards each event to the bus + WS.
    Must emit events in agent order: orchestrator -> monitor -> diagnosis -> spare_parts
    -> scheduler -> energy -> decision, and finish with a decision RESULT (OptionsResponse).
    Must finish < 60 s. On LLM failure, fall back to replay from fixtures/agent_events.jsonl."""
    def run(self, incident: Incident) -> AsyncIterator[AgentEvent]: ...
    async def on_feedback(self, incident: Incident, feedback: Feedback) -> None: ...
    async def on_approval(self, incident: Incident, approval: Approval) -> None: ...
