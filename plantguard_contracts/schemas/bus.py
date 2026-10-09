"""Internal event bus (Redis pub/sub). Channel names and payload type per channel."""
from datetime import datetime
from .common import AssetId, Contract, IncidentId, LineId, PlantId
from .incidents import Severity


class AlertRaised(Contract):
    """Published by the backend health watcher when ML risk crosses a threshold."""
    asset_id: AssetId
    plant_id: PlantId
    line_id: LineId
    severity: Severity
    risk_72h: float
    reason: str
    ts: datetime
    incident_id: IncidentId | None = None  # set once an incident is opened


class SceneChanged(Contract):
    scene: str
    ts: datetime


# channel template -> payload model name
CHANNELS: dict[str, str] = {
    "ot.telemetry.{plant_id}.{line_id}": "TelemetryMessage",  # simulator -> backend, ML scorer
    "ml.health.{asset_id}": "AssetHealth",                    # ML scorer -> backend WS, monitor agent
    "alerts.raised": "AlertRaised",                           # health watcher -> incidents, UI
    "incident.{incident_id}.events": "AgentEvent",            # agent runtime -> WS stream
    "incident.{incident_id}.status": "Incident",              # backend -> UI
    "sim.scene": "SceneChanged",                              # simulator -> everyone
}
