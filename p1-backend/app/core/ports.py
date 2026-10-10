"""Backend-internal ports (interfaces.py covers the cross-team ones). Services depend on these, never on
concrete classes, so a fixture can be swapped for SAP / OPC-UA / a real model without touching callers."""
from datetime import datetime
from typing import Protocol

from plantguard_contracts.schemas.ml import AssetHealth, FeatureWindow, ModelInfo
from plantguard_contracts.schemas.quality import Inspection
from plantguard_contracts.interfaces import QualityConnector
from plantguard_contracts.schemas.simulator import Scenario


class SceneView(Protocol):
    """Read-only view of the simulator scene (ISP: connectors never need the setter)."""
    @property
    def scenario(self) -> Scenario:
        """The active simulator scenario."""

    @property
    def changed_at(self) -> datetime:
        """When the scenario last changed (used to ramp the warning scene over time)."""

    def is_calm(self) -> bool:
        """True for calm, reset and resolved scenes (everything green)."""


class HealthProvider(Protocol):
    """Source of asset health: fixtures today, the real P4 model when scoring is enabled."""

    def get_health(self, asset_id: str) -> AssetHealth:
        """Latest AssetHealth for one asset; KeyError if unknown."""

    def predict(self, window: FeatureWindow) -> AssetHealth:
        """Score a caller-supplied FeatureWindow (POST /ml/predict)."""

    def models(self) -> list[ModelInfo]:
        """Info about the models that are loaded."""


class InspectionConnector(QualityConnector, Protocol):
    """QualityConnector plus inspections (the shared interface only has defects)."""

    def get_inspections(self, line_id: str) -> list[Inspection]:
        """Inspection records for a line."""
