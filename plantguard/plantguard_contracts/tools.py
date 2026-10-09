"""Tool registry: the ONLY way agents touch plant data. [BE] implements, [AG] calls.
Each tool maps to a domain endpoint, has typed input/output, and an agent allow-list."""
from dataclasses import dataclass, field

from pydantic import BaseModel

from .schemas.common import Contract
from .schemas.energy import Co2Factor, LineConsumption, Tariff
from .schemas.engineering import AssetBom, DocSearchRequest, DocSearchResponse
from .schemas.erp import Part, StockTransfer, StockTransferCreate, Supplier
from .schemas.maintenance import WorkOrder, WorkOrderCreate
from .schemas.mes import ProductionOrder, ScheduleSlot
from .schemas.ml import AssetHealth
from .schemas.ot import Alarm, Signal, SignalSeries
from .schemas.quality import DefectSummary
from .schemas.workforce import Technician


# ---- tool inputs ----
class AssetArgs(Contract):
    asset_id: str

class SignalArgs(Contract):
    asset_id: str
    signals: list[Signal]
    minutes: int = 360

class PlantArgs(Contract):
    plant_id: str | None = None

class LineArgs(Contract):
    line_id: str
    hours: int = 24

class SkuArgs(Contract):
    sku: str

class SupplierArgs(Contract):
    supplier_id: str

class TechArgs(Contract):
    skill: str | None = None
    on_shift: bool | None = None

class NoArgs(Contract):
    pass

# ---- list outputs wrapped so every tool returns an object ----
class SignalList(Contract):
    items: list[SignalSeries]

class AlarmList(Contract):  # same shape as Page[Alarm] returned by GET /ot/alarms
    items: list[Alarm]
    total: int
    next_cursor: str | None = None

class WorkOrderList(Contract):
    items: list[WorkOrder]

class OrderList(Contract):
    items: list[ProductionOrder]

class ScheduleList(Contract):
    items: list[ScheduleSlot]

class TechnicianList(Contract):
    items: list[Technician]

class TariffList(Contract):
    items: list[Tariff]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # shown to the LLM
    input: type[BaseModel]
    output: type[BaseModel]
    endpoint: str
    agents: frozenset[str]
    writes: bool = False  # True -> needs approval_token, only called by backend executor
    timeout_s: float = 3.0


def _t(name, desc, inp, out, endpoint, agents, writes=False):
    return ToolSpec(name, desc, inp, out, endpoint, frozenset(agents), writes)


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    _t("get_asset_health", "ML risk, RUL and top SHAP features for an asset", AssetArgs, AssetHealth,
       "GET /ml/assets/{asset_id}/health", {"monitor", "diagnosis", "decision"}),
    _t("get_signals", "Sensor time series for an asset", SignalArgs, SignalList,
       "GET /ot/assets/{asset_id}/signals", {"monitor", "diagnosis"}),
    _t("get_alarms", "Active alarms for a plant", PlantArgs, AlarmList, "GET /ot/alarms", {"monitor", "diagnosis"}),
    _t("search_manuals", "Search machine manuals and past work orders; returns cited chunks",
       DocSearchRequest, DocSearchResponse, "POST /engineering/docs/search", {"diagnosis"}),
    _t("get_maintenance_history", "Past work orders and failures for an asset", AssetArgs, WorkOrderList,
       "GET /maintenance/assets/{asset_id}/history", {"diagnosis"}),
    _t("get_defects", "Quality defects for a line", LineArgs, DefectSummary,
       "GET /quality/lines/{line_id}/defects", {"diagnosis"}),
    _t("get_bom", "Bill of materials for an asset", AssetArgs, AssetBom,
       "GET /engineering/assets/{asset_id}/bom", {"diagnosis", "spare_parts"}),
    _t("check_stock", "Part stock in every plant, unit cost and supplier lead time", SkuArgs, Part,
       "GET /erp/parts/{sku}", {"spare_parts"}),
    _t("get_supplier", "Supplier lead times and express option", SupplierArgs, Supplier,
       "GET /erp/suppliers/{supplier_id}", {"spare_parts"}),
    _t("get_orders", "Production orders and due dates", LineArgs, OrderList, "GET /mes/orders",
       {"scheduler", "decision"}),
    _t("get_schedule", "Planned production slots for a line", LineArgs, ScheduleList,
       "GET /mes/lines/{line_id}/schedule", {"scheduler"}),
    _t("find_technicians", "Technicians by skill and shift", TechArgs, TechnicianList,
       "GET /workforce/technicians", {"scheduler"}),
    _t("get_energy", "Energy use, cost and CO2 for a line", LineArgs, LineConsumption,
       "GET /energy/lines/{line_id}/consumption", {"energy"}),
    _t("get_tariffs", "Peak and off-peak electricity prices", NoArgs, TariffList, "GET /energy/tariffs", {"energy"}),
    _t("get_co2_factor", "Grid CO2 kg per kWh", NoArgs, Co2Factor, "GET /energy/co2-factor", {"energy"}),
    # write tools: executed by the backend after approval, never by an LLM directly
    _t("create_work_order", "Create maintenance work order", WorkOrderCreate, WorkOrder,
       "POST /maintenance/work-orders", {"executor"}, writes=True),
    _t("create_stock_transfer", "Move parts between plants", StockTransferCreate, StockTransfer,
       "POST /erp/stock-transfers", {"executor"}, writes=True),
]}


def tools_for(agent: str) -> list[ToolSpec]:
    return [t for t in TOOLS.values() if agent in t.agents]


def openai_tool_schema(spec: ToolSpec) -> dict:
    """Function-calling schema passed to llm.client.chat(tools=...)."""
    return {"type": "function", "function": {
        "name": spec.name, "description": spec.description,
        "parameters": spec.input.model_json_schema()}}
