"""Fixture-backed connectors (BE-3). Each class implements one Protocol from plantguard_contracts.interfaces.
Swap any of them for a real system (SAP, OPC-UA, MES...) in container.py; routers and agents never change."""
from datetime import datetime, timedelta
from typing import AsyncIterator

from pydantic import TypeAdapter

from plantguard_contracts.schemas.common import Health
from plantguard_contracts.schemas.energy import Co2Factor, LineConsumption, Tariff
from plantguard_contracts.schemas.engineering import AssetBom, DocSearchRequest, DocSearchResponse
from plantguard_contracts.schemas.erp import (Part, StockTransfer, StockTransferCreate, Supplier, TransferMode,
                                              TransferStatus)
from plantguard_contracts.schemas.maintenance import WorkOrder, WorkOrderCreate, WorkOrderStatus
from plantguard_contracts.schemas.mes import LineOEE, ProductionOrder, ScheduleSlot
from plantguard_contracts.schemas.ot import Alarm, Signal, SignalSeries, TelemetryMessage
from plantguard_contracts.schemas.plants import Plant
from plantguard_contracts.schemas.quality import DefectSummary, Inspection
from plantguard_contracts.schemas.simulator import Scenario
from plantguard_contracts.schemas.workforce import Shift, Technician

from app.core.common import Clock, IdSequence, utcnow
from app.services.plant_directory import PlantDirectory
from app.services.line_speeds import LineSpeeds
from app.core.ports import SceneView
from app.connectors.store import FixtureStore

STANDARD_TRANSFER = (350.0, 20)  # (EUR, hours)
EXPRESS_TRANSFER = (900.0, 6)


def _parse(tp, data):
    """Validate raw JSON into a typed pydantic value (e.g. list[Plant])."""
    return TypeAdapter(tp).validate_python(data)


class FixturePlants:  # implements PlantConnector
    """Plant hierarchy from plants.json; in calm scenes every line and asset is shown green."""

    def __init__(self, store: FixtureStore, scene: SceneView):
        """store: fixture files; scene: tells whether the scene is calm."""
        self._store, self._scene = store, scene

    def list_plants(self) -> list[Plant]:
        """All plants; calm scenes force health OK and risk 0.04."""
        data = self._store.load("plants")
        if self._scene.is_calm():  # scene 1: everything green
            for plant in data:
                for line in plant["lines"]:
                    line["health"] = Health.OK.value
                    for station in line["stations"]:
                        for asset in station["assets"]:
                            asset.update(health=Health.OK.value, risk_72h=0.04)
        return _parse(list[Plant], data)


class FixtureOT:  # implements OTConnector
    """Signals, alarms and live telemetry for assets."""

    def __init__(self, store: FixtureStore, scene: SceneView, directory: PlantDirectory, telemetry):
        """telemetry: object with stream(plant_id, line_id) -> AsyncIterator[TelemetryMessage] (the OT gateway)."""
        self._store, self._scene, self._directory, self._telemetry = store, scene, directory, telemetry

    def get_signals(self, asset_id: str, signals: list[Signal], minutes: int) -> list[SignalSeries]:
        """Stored signal history, cut to the last `minutes` (5-minute samples), filtered by signal."""
        series = _parse(list[SignalSeries], self._store.load(f"signals_{asset_id}"))
        n = max(1, minutes // 5)  # fixtures are sampled every 5 minutes
        return [s.model_copy(update={"points": s.points[-n:]}) for s in series if not signals or s.signal in signals]

    def get_alarms(self, plant_id: str | None, active: bool) -> list[Alarm]:
        """Alarms (none in calm/reset), filtered by plant and, if active, only unacknowledged ones."""
        if self._scene.scenario in (Scenario.CALM, Scenario.RESET):
            return []
        alarms = _parse(list[Alarm], self._store.load("alarms"))
        if plant_id:
            alarms = [a for a in alarms if self._plant_of(a.asset_id) == plant_id]
        return [a for a in alarms if not active or not a.acknowledged]

    def subscribe(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]:
        """Live telemetry for a line, taken from the OT gateway."""
        return self._telemetry.stream(plant_id, line_id)

    def _plant_of(self, asset_id: str) -> str | None:
        """Plant id of an asset, or None if the asset is unknown."""
        try:
            return self._directory.plant_of_asset(asset_id)
        except KeyError:
            return None


class FixtureERP:  # implements ERPConnector
    """Parts, suppliers and stock transfers (transfers are kept in memory)."""

    def __init__(self, store: FixtureStore, clock: Clock = utcnow):
        """store: fixture files; clock: timestamps; stock transfer ids start at STO-4500012."""
        self._store, self._clock = store, clock
        self._ids = IdSequence("STO-", 4500012)
        self.transfers: list[StockTransfer] = []

    def get_part(self, sku: str) -> Part:
        """Stock, location and supplier of a part by SKU."""
        return Part(**self._store.load(f"part_{sku}"))

    def get_supplier(self, supplier_id: str) -> Supplier:
        """Supplier details by id."""
        return Supplier(**self._store.load(f"supplier_{supplier_id}"))

    def create_stock_transfer(self, req: StockTransferCreate) -> StockTransfer:
        """Create a transfer: standard EUR 350 / 20 h, express EUR 900 / 6 h; remembers it."""
        cost, eta_h = EXPRESS_TRANSFER if req.mode == TransferMode.EXPRESS else STANDARD_TRANSFER
        transfer = StockTransfer(id=self._ids.next(), sku=req.sku, from_plant=req.from_plant, to_plant=req.to_plant,
                                 quantity=req.quantity, mode=req.mode, status=TransferStatus.CREATED,
                                 eta=self._clock() + timedelta(hours=eta_h), cost={"amount": cost})
        self.transfers.append(transfer)
        return transfer

    def reset(self) -> None:
        """Forget created transfers and restart the id sequence."""
        self.transfers.clear()
        self._ids.reset()


class FixtureMES:  # implements MESConnector
    """Production orders, schedule, OEE and line speed."""

    def __init__(self, store: FixtureStore, speeds: LineSpeeds):
        """store: fixture files; speeds: shared line speed store."""
        self._store, self._speeds = store, speeds

    def get_orders(self, line_id: str | None) -> list[ProductionOrder]:
        """Production orders, optionally only for one line."""
        orders = _parse(list[ProductionOrder], self._store.load("orders"))
        return [o for o in orders if not line_id or o.line_id == line_id]

    def get_schedule(self, line_id: str, hours: int) -> list[ScheduleSlot]:
        """Planned schedule slots of a line."""
        return _parse(list[ScheduleSlot], self._store.load(f"schedule_{line_id}"))

    def get_oee(self, line_id: str) -> LineOEE:
        """OEE figures of a line."""
        return LineOEE(**self._store.load(f"oee_{line_id}"))

    def set_line_speed(self, line_id: str, speed_pct: int, start: datetime, end: datetime | None) -> None:
        """Store the new line speed (the synthetic telemetry reads it)."""
        self._speeds.set(line_id, speed_pct)


class FixtureMaintenance:  # implements MaintenanceConnector
    """Work-order history plus work orders created during this run."""

    def __init__(self, store: FixtureStore, clock: Clock = utcnow):
        """store: fixture files; clock: timestamps; ids start at WO-2026-0153."""
        self._store, self._clock = store, clock
        self._ids = IdSequence("WO-2026-", 153, width=4)
        self.created: list[WorkOrder] = []

    def get_history(self, asset_id: str) -> list[WorkOrder]:
        """Historic work orders of an asset plus ones created in this run."""
        history = _parse(list[WorkOrder], self._store.load(f"history_{asset_id}"))
        return history + [w for w in self.created if w.asset_id == asset_id]

    def create_work_order(self, req: WorkOrderCreate) -> WorkOrder:
        """Create a SCHEDULED work order from the draft (token field dropped) and remember it."""
        wo = WorkOrder(id=self._ids.next(), status=WorkOrderStatus.SCHEDULED, created_at=self._clock(),
                       **req.model_dump(exclude={"approval_token"}))
        self.created.append(wo)
        return wo

    def reset(self) -> None:
        """Forget created work orders and restart the id sequence."""
        self.created.clear()
        self._ids.reset()


class FixtureQuality:  # implements ports.InspectionConnector
    """Defect summaries and inspections."""

    def __init__(self, store: FixtureStore):
        """store: fixture files."""
        self._store = store

    def get_defects(self, line_id: str, hours: int) -> DefectSummary:
        """Defect summary of a line."""
        return DefectSummary(**self._store.load(f"defects_{line_id}"))

    def get_inspections(self, line_id: str) -> list[Inspection]:
        """Inspections of a line (none in the fixtures yet)."""
        return []


class FixtureEngineering:  # implements EngineeringConnector
    """Bill of materials per asset."""

    def __init__(self, store: FixtureStore):
        """store: fixture files."""
        self._store = store

    def get_bom(self, asset_id: str) -> AssetBom:
        """BOM of an asset."""
        return AssetBom(**self._store.load(f"bom_{asset_id}"))


class FixtureRetriever:  # implements Retriever. P2's RAG replaces it.
    """Stand-in for P2's document search: always returns the bearing-manual fixture."""

    def __init__(self, store: FixtureStore):
        """store: fixture files."""
        self._store = store

    def search(self, req: DocSearchRequest) -> DocSearchResponse:
        """Return the fixture chunks (up to top_k) with the caller's query filled in."""
        data = self._store.load("docsearch_bearing")
        return DocSearchResponse(**dict(data, query=req.query, chunks=data["chunks"][: req.top_k]))


class FixtureWorkforce:  # implements WorkforceConnector
    """Shifts and technicians."""

    def __init__(self, store: FixtureStore):
        """store: fixture files."""
        self._store = store

    def get_shifts(self, plant_id: str) -> list[Shift]:
        """Shift roster of a plant."""
        return _parse(list[Shift], self._store.load(f"shifts_{plant_id}"))

    def find_technicians(self, skill: str | None, on_shift: bool | None) -> list[Technician]:
        """Technicians filtered by skill and on-shift flag."""
        techs = _parse(list[Technician], self._store.load("technicians"))
        return [t for t in techs if (not skill or skill in t.skills) and (on_shift is None or t.on_shift == on_shift)]


class FixtureEnergy:  # implements EnergyConnector
    """Energy consumption, tariffs and CO2 factor."""

    def __init__(self, store: FixtureStore):
        """store: fixture files."""
        self._store = store

    def get_consumption(self, line_id: str, hours: int) -> LineConsumption:
        """Energy use of a line."""
        return LineConsumption(**self._store.load(f"energy_{line_id}"))

    def get_tariffs(self) -> list[Tariff]:
        """Electricity tariff bands."""
        return _parse(list[Tariff], self._store.load("tariffs"))

    def get_co2_factor(self) -> Co2Factor:
        """Grid CO2 factor."""
        return Co2Factor(**self._store.load("co2_factor"))
