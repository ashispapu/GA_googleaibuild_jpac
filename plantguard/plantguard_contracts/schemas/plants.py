from .common import AssetId, Contract, Health, LineId, MachineState, OrderId, PlantId, Probability, StationId


class Asset(Contract):
    id: AssetId
    name: str
    type: str  # spindle | press | conveyor | robot | inspection
    state: MachineState
    health: Health
    risk_72h: Probability
    position: tuple[float, float]  # floor-map x,y in metres from plant origin


class Station(Contract):
    id: StationId
    name: str
    order: int  # position in the line flow, 1 = first
    assets: list[Asset]


class Line(Contract):
    id: LineId
    plant_id: PlantId
    name: str
    product: str
    state: MachineState
    health: Health
    speed_pct: int
    oee: float
    current_order_id: OrderId | None = None
    stations: list[Station]


class Plant(Contract):
    id: PlantId
    name: str
    location: str
    lines: list[Line]
