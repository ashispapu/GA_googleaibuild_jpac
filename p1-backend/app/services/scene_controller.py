"""Simulator scene state. Observers (incident lifecycle, line speeds...) subscribe instead of being hard-wired."""
from typing import Awaitable, Callable

from plantguard_contracts.interfaces import EventBus
from plantguard_contracts.schemas.bus import SceneChanged
from plantguard_contracts.schemas.simulator import Scenario

from app.core.common import Clock, utcnow

SceneListener = Callable[[Scenario], Awaitable[None]]
CALM_SCENES = (Scenario.CALM, Scenario.RESET, Scenario.RESOLVED)


class SceneController:  # implements ports.SceneView
    """Holds the current simulator scene and tells registered listeners when it changes."""

    def __init__(self, bus: EventBus, initial: Scenario = Scenario.CRISIS, clock: Clock = utcnow):
        """bus: where sim.scene is published; initial: starting scene; clock: timestamps."""
        self._bus, self._clock = bus, clock
        self._scenario, self._changed_at = initial, clock()
        self._listeners: list[SceneListener] = []

    @property
    def scenario(self) -> Scenario:
        """The active scenario."""
        return self._scenario

    @property
    def changed_at(self):
        """When the scene last changed."""
        return self._changed_at

    def is_calm(self) -> bool:
        """True for calm, reset or resolved."""
        return self._scenario in CALM_SCENES

    def add_listener(self, listener: SceneListener) -> None:
        """Register an async callback(scenario) to run on every change."""
        self._listeners.append(listener)

    async def set(self, scenario: Scenario) -> None:
        """Switch scene, publish `sim.scene`, then run every listener in order."""
        self._scenario, self._changed_at = scenario, self._clock()
        await self._bus.publish("sim.scene", SceneChanged(scene=scenario.value, ts=self._changed_at))
        for listener in self._listeners:
            await listener(scenario)
