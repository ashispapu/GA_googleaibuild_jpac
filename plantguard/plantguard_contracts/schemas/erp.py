from datetime import datetime
from enum import Enum
from pydantic import Field
from .common import Contract, IncidentId, Money, PlantId, Sku


class PlantStock(Contract):
    plant_id: PlantId
    quantity: int = Field(ge=0)
    storage_location: str


class Part(Contract):
    sku: Sku
    description: str
    unit_cost: Money
    stock: list[PlantStock]
    supplier_id: str
    supplier_lead_time_days: int


class Supplier(Contract):
    id: str
    name: str
    lead_time_days: int
    express_available: bool
    express_lead_time_days: int | None = None


class TransferMode(str, Enum):
    STANDARD = "standard"  # next scheduled truck
    EXPRESS = "express"    # dedicated courier


class TransferStatus(str, Enum):
    CREATED = "created"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"


class StockTransferDraft(Contract):
    sku: Sku
    from_plant: PlantId
    to_plant: PlantId
    quantity: int = Field(ge=1)
    mode: TransferMode
    incident_id: IncidentId


class StockTransferCreate(StockTransferDraft):
    approval_token: str


class StockTransfer(Contract):
    id: str = Field(pattern=r"^STO-\d+$", examples=["STO-4500012"])
    sku: Sku
    from_plant: PlantId
    to_plant: PlantId
    quantity: int
    mode: TransferMode
    status: TransferStatus
    eta: datetime
    cost: Money
