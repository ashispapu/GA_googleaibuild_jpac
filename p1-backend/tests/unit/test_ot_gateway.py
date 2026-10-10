from datetime import timedelta

import pytest

from app.core.config import Settings
from app.connectors.telemetry.tag_map import TagMap
from app.services.ot_gateway import QualityPolicy, StalenessWatchdog
from data import NOW
from plantguard_contracts.schemas.common import DataQuality, MachineState
from plantguard_contracts.schemas.ot import Signal, TelemetryMessage


def msg(asset="EQ-3012", ts=NOW, quality=DataQuality.GOOD, **values):
    """Helper: build a TelemetryMessage with the given values."""
    return TelemetryMessage(asset_id=asset, line_id="L3", ts=ts, state=MachineState.RUN, quality=quality,
                            values={Signal(k): v for k, v in (values or {"vibration_rms": 2.1}).items()})


@pytest.fixture()
def policy():
    """A QualityPolicy using the real tag map."""
    return QualityPolicy(TagMap.load(Settings().tag_map_path))


def test_out_of_range_and_stale_flags(policy):
    """Values outside the range are out_of_range; old messages are stale."""
    assert policy.annotate(msg(vibration_rms=2.1), NOW).quality == DataQuality.GOOD
    assert policy.annotate(msg(vibration_rms=99), NOW).quality == DataQuality.OUT_OF_RANGE
    assert policy.annotate(msg(), NOW + timedelta(seconds=6)).quality == DataQuality.STALE


def test_watchdog_flags_silent_assets_once():
    """A silent asset is reported stale once, not repeatedly."""
    wd = StalenessWatchdog(5)
    wd.observe(msg())
    assert wd.stale(NOW + timedelta(seconds=2)) == []
    stale = wd.stale(NOW + timedelta(seconds=6))
    assert [m.quality for m in stale] == [DataQuality.STALE]
    wd.observe(stale[0])
    assert wd.stale(NOW + timedelta(seconds=7)) == []
