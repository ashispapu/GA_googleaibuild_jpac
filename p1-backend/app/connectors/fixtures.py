"""Fixture-backed connectors (BE-3). Implement the Protocols in plantguard_contracts.interfaces.
Later swap any class for a real one (SAP, OPC-UA...) without touching routers or agents."""
import json
from pathlib import Path

from plantguard_contracts.schemas.erp import Part, StockTransfer, StockTransferCreate, Supplier

FX = Path(__file__).resolve().parents[3] / "fixtures" / "story"


def _load(name: str):
    p = FX / f"{name}.json"
    if not p.exists():
        raise KeyError(name)  # routers turn KeyError into 404 NOT_FOUND
    return json.loads(p.read_text())


class FixtureERP:  # implements ERPConnector
    def get_part(self, sku: str) -> Part:
        return Part(**_load(f"part_{sku}"))

    def get_supplier(self, supplier_id: str) -> Supplier:
        return Supplier(**_load(f"supplier_{supplier_id}"))

    def create_stock_transfer(self, req: StockTransferCreate) -> StockTransfer:
        raise NotImplementedError  # BE-3

# TODO: FixtureOT, FixtureMES, FixtureMaintenance, FixtureQuality, FixtureEngineering,
#       FixtureWorkforce, FixtureEnergy, FixturePlants — same pattern.
