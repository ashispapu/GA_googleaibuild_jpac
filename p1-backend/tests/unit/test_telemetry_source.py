import asyncio

from data import NOW
from plantguard_contracts.schemas.ot import Signal
from plantguard_contracts.schemas.simulator import Scenario


def test_synthetic_source_follows_the_scene(container):
    """Calm is baseline; warning raises vibration; lower line speed lowers load."""
    src, scene = container.telemetry_source, container.scene
    asyncio.run(scene.set(Scenario.CALM))
    calm = src.sample("EQ-3012", "L3").values[Signal.VIBRATION_RMS]
    asyncio.run(scene.set(Scenario.CRISIS))
    crisis = src.sample("EQ-3012", "L3").values[Signal.VIBRATION_RMS]
    assert calm < 2.6 and 3.9 < crisis < 4.7
    container.mes.set_line_speed("L3", 70, NOW, None)  # approved speed change lowers load and vibration growth
    assert src.sample("EQ-3012", "L3").values[Signal.VIBRATION_RMS] < crisis - 0.1
