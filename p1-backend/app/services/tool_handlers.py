"""Tool name -> backend call. Adding a tool = one line here + one entry in plantguard_contracts/tools.py (OCP)."""
from dataclasses import dataclass
from typing import Any, Callable

from pydantic_core import to_jsonable_python

from plantguard_contracts.interfaces import (EnergyConnector, EngineeringConnector, ERPConnector, MaintenanceConnector,
                                             MESConnector, OTConnector, Retriever, WorkforceConnector)
from app.core.ports import HealthProvider, InspectionConnector

ToolHandler = Callable[[Any], Any]  # validated input model -> result (model, list, or dict)


@dataclass
class ToolBackends:
    """All the data sources the tools read from or write to, bundled for build_handlers."""

    ot: OTConnector
    erp: ERPConnector
    mes: MESConnector
    maintenance: MaintenanceConnector
    quality: InspectionConnector
    engineering: EngineeringConnector
    retriever: Retriever
    workforce: WorkforceConnector
    energy: EnergyConnector
    health: HealthProvider
    incident_context: Callable[[str], Any] | None = None  # get_incident (repair workflow or basic)


def build_handlers(b: ToolBackends) -> dict[str, ToolHandler]:
    """Map every tool name to a function calling the right backend; writes are reached only via ToolGateway."""
    def alarms(a):
        """Active alarms of a plant, wrapped as {items, total}."""
        items = b.ot.get_alarms(a.plant_id, True)
        return {"items": items, "total": len(items)}

    return {
        "get_asset_health": lambda a: b.health.get_health(a.asset_id),
        "get_signals": lambda a: b.ot.get_signals(a.asset_id, a.signals, a.minutes),
        "get_alarms": alarms,
        "search_manuals": lambda a: b.retriever.search(a),
        "get_maintenance_history": lambda a: b.maintenance.get_history(a.asset_id),
        "get_defects": lambda a: b.quality.get_defects(a.line_id, a.hours, a.plant_id),
        "get_incident": lambda a: _context(b, a.incident_id),
        "get_bom": lambda a: b.engineering.get_bom(a.asset_id),
        "check_stock": lambda a: b.erp.get_part(a.sku),
        "get_supplier": lambda a: b.erp.get_supplier(a.supplier_id),
        "get_orders": lambda a: b.mes.get_orders(a.line_id, a.plant_id),
        "get_schedule": lambda a: b.mes.get_schedule(a.line_id, a.hours, a.plant_id),
        "find_technicians": lambda a: b.workforce.find_technicians(a.skill, a.on_shift, a.plant_id),
        "get_energy": lambda a: b.energy.get_consumption(a.line_id, a.hours, a.plant_id),
        "get_tariffs": lambda a: b.energy.get_tariffs(),
        "get_co2_factor": lambda a: b.energy.get_co2_factor(),
        # writes: reached only through ToolGateway after the approval check
        "create_work_order": lambda a: b.maintenance.create_work_order(a),
        "create_stock_transfer": lambda a: b.erp.create_stock_transfer(a),
    }


def to_plain(result: Any) -> dict:
    """Models/lists -> JSON-able dict; bare lists are wrapped as {"items": [...]} like the tool outputs expect."""
    data = to_jsonable_python(result)
    return {"items": data} if isinstance(data, list) else data



def _context(b: ToolBackends, incident_id: str):
    if b.incident_context is None:
        raise KeyError(incident_id)
    return b.incident_context(incident_id)
