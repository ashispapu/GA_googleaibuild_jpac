"""Telemetry source used until the P4 simulator publishes on MQTT. Same TelemetryMessage shape and scene behaviour
(C6): calm = baseline; warning ramps EQ-3012 vibration 2.1 -> 4.3 mm/s and bearing temp +8 C over 60 s;
at 70% line speed the load, power and vibration growth drop."""
import asyncio
import random
from typing import AsyncIterator

from plantguard_contracts.schemas.common import MachineState
from plantguard_contracts.schemas.ot import Signal, TelemetryMessage
from plantguard_contracts.schemas.simulator import Scenario

from app.core.common import Clock, utcnow
from app.services.plant_directory import PlantDirectory
from app.core.ports import SceneView
from app.services.line_speeds import LineSpeeds

FAULT_SCENES = (Scenario.WARNING, Scenario.CRISIS, Scenario.FULL_STORY)
RAMP_SECONDS = 60.0


class SyntheticTelemetrySource:  # implements the TelemetrySource used by OTGateway
    """Generates fake 1 Hz telemetry for every asset of a line, driven by the scene and line speed."""

    def __init__(self, directory: PlantDirectory, scene: SceneView, speeds: LineSpeeds, hero_asset_id: str,
                 interval_s: float = 1.0, clock: Clock = utcnow, rng: random.Random | None = None):
        """directory: asset lookup; scene/speeds: drive the signals; hero_asset_id: asset that develops the fault;
        interval_s: seconds between samples; rng: noise source."""
        self._dir, self._scene, self._speeds, self._hero = directory, scene, speeds, hero_asset_id
        self._interval, self._clock, self._rng = interval_s, clock, rng or random.Random()

    async def subscribe(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]:
        """Endless stream: one message per asset of the line every interval."""
        _, assets = self._dir.line_assets(line_id, [plant_id])
        while True:
            for asset_id in assets:
                yield self.sample(asset_id, line_id)
            await asyncio.sleep(self._interval)

    def sample(self, asset_id: str, line_id: str) -> TelemetryMessage:
        """Build one reading. The hero asset ramps vibration, temperature and kurtosis in warning/crisis scenes; lower
        line speed reduces load, power and vibration growth."""
        now, g = self._clock(), self._rng.gauss
        faulty = asset_id == self._hero and self._scene.scenario in FAULT_SCENES
        ramp = 1.0 if faulty else 0.0
        if faulty and self._scene.scenario == Scenario.WARNING:
            ramp = min(1.0, (now - self._scene.changed_at).total_seconds() / RAMP_SECONDS)
        sf = self._speeds.get(line_id) / 100.0
        vib = 2.1 + 2.2 * ramp * (0.5 + 0.5 * sf) + g(0, 0.06)
        values = {
            Signal.VIBRATION_RMS: vib,
            Signal.VIBRATION_PEAK: vib * 3.1 + g(0, 0.1),
            Signal.VIBRATION_KURTOSIS: 3.0 + 2.1 * ramp + g(0, 0.05),
            Signal.BEARING_TEMP: 58 + 8 * ramp + g(0, 0.2),
            Signal.MOTOR_CURRENT: (21 - 4 * (1 - sf)) + g(0, 0.1),
            Signal.POWER_KW: 11 * (1.08 if faulty else 1.0) * (0.4 + 0.6 * sf) + g(0, 0.05),
            Signal.SPINDLE_RPM: 12000 * sf + g(0, 10),
        }
        return TelemetryMessage(asset_id=asset_id, line_id=line_id, ts=now, state=MachineState.RUN,
                                values={k: round(v, 3) for k, v in values.items()})
