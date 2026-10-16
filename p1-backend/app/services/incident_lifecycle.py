"""Reacts to things that open or close incidents: simulator scenes and ML alerts."""
import logging

from plantguard_contracts.interfaces import EventBus
from plantguard_contracts.schemas.bus import AlertRaised
from plantguard_contracts.schemas.incidents import IncidentCreate, IncidentTrigger
from plantguard_contracts.schemas.simulator import Scenario

from app.services.incident_service import SYSTEM_ACTOR, IncidentService

log = logging.getLogger("plantguard.lifecycle")


class SceneIncidentLifecycle:
    """Scene listener: calm/reset clears incidents; warning/crisis lets the 'health watcher' open the hero incident."""

    def __init__(self, service: IncidentService, hero_asset_id: str):
        """service: used to open/clear incidents; hero_asset_id: asset the scenes act on."""
        self._service, self._hero = service, hero_asset_id

    async def on_scene(self, scenario: Scenario) -> None:
        """Scene listener: calm/reset clears incidents; warning/crisis/full_story opens the hero asset's incident;
        resolved closes active ones."""
        if scenario in (Scenario.RESET, Scenario.CALM):
            self._service.reset()
        elif scenario in (Scenario.WARNING, Scenario.CRISIS, Scenario.FULL_STORY):
            await self._service.open(IncidentCreate(asset_id=self._hero, trigger=IncidentTrigger.ML_ALERT),
                                     SYSTEM_ACTOR)
        elif scenario == Scenario.RESOLVED:
            await self._service.resolve_active()


class AlertIncidentOpener:
    """Consumes `alerts.raised` from the scoring loop and opens an incident per alert."""

    def __init__(self, service: IncidentService, bus: EventBus, ignore_alert=None):
        """service: opens incidents; bus: where `alerts.raised` arrives."""
        self._service, self._bus, self._ignore = service, bus, ignore_alert

    async def run(self) -> None:
        """Background loop: for each alert on the bus, open an incident for that asset. A bad alert is logged and
        skipped."""
        async for payload in self._bus.subscribe("alerts.raised"):
            try:
                alert = AlertRaised.model_validate(payload)
                if self._ignore is not None and self._ignore(alert):
                    continue
                await self._service.open(IncidentCreate(asset_id=alert.asset_id, trigger=IncidentTrigger.ML_ALERT),
                                         SYSTEM_ACTOR)
            except Exception:
                log.exception("could not open incident from alert")
