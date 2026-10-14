"""OT gateway (BE-4): turns raw source messages into trusted ones. One-way: nothing here writes to the OT side.
Rules (C6): value outside the tag-map range -> out_of_range; no update for 5 s -> stale."""
from datetime import datetime, timedelta
from typing import AsyncIterator, Protocol

from plantguard_contracts.schemas.common import DataQuality
from plantguard_contracts.schemas.ot import TelemetryMessage

from app.core.common import Clock, utcnow
from app.connectors.telemetry.tag_map import TagMap

STALE_AFTER_S = 5.0


class TelemetrySource(Protocol):
    """Anything that can stream TelemetryMessages for a line (synthetic now, MQTT later)."""

    def subscribe(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]:
        """Async stream of raw messages for one line."""


class QualityPolicy:
    """Decides a message's data quality: good, stale or out_of_range."""

    def __init__(self, tags: TagMap, stale_after_s: float = STALE_AFTER_S):
        """tags: tag map with valid ranges; stale_after_s: max age before a message is stale."""
        self._tags, self._stale_after = tags, timedelta(seconds=stale_after_s)

    def annotate(self, msg: TelemetryMessage, now: datetime) -> TelemetryMessage:
        """Return the message marked STALE if too old, or OUT_OF_RANGE if any value is outside its tag-map range;
        otherwise unchanged. Messages not GOOD are left as they are."""
        if msg.quality != DataQuality.GOOD:
            return msg
        if now - msg.ts > self._stale_after:
            return msg.model_copy(update={"quality": DataQuality.STALE})
        for signal, value in msg.values.items():
            rng = self._tags.range_of(msg.asset_id, signal)
            if rng and not (rng[0] <= value <= rng[1]):
                return msg.model_copy(update={"quality": DataQuality.OUT_OF_RANGE})
        return msg


class StalenessWatchdog:
    """Remembers the last message per asset; reports assets silent for too long as STALE copies."""

    def __init__(self, stale_after_s: float = STALE_AFTER_S):
        """stale_after_s: silence allowed before an asset is reported stale."""
        self._after = timedelta(seconds=stale_after_s)
        self._last: dict[str, TelemetryMessage] = {}

    def observe(self, msg: TelemetryMessage) -> None:
        """Remember the latest message of an asset."""
        self._last[msg.asset_id] = msg

    def stale(self, now: datetime) -> list[TelemetryMessage]:
        """STALE copies of the last messages of assets silent for longer than the limit."""
        return [m.model_copy(update={"quality": DataQuality.STALE}) for m in self._last.values()
                if now - m.ts > self._after and m.quality != DataQuality.STALE]


class OTGateway:
    """One-way gate between the OT source and the rest of the backend. It never writes to machines."""

    def __init__(self, source: TelemetrySource, policy: QualityPolicy, clock: Clock = utcnow):
        """source: raw telemetry; policy: quality rules; clock: current time."""
        self._source, self._policy, self._clock = source, policy, clock

    async def stream(self, plant_id: str, line_id: str) -> AsyncIterator[TelemetryMessage]:
        """Stream a line's telemetry with each message's quality set by the policy."""
        async for msg in self._source.subscribe(plant_id, line_id):
            yield self._policy.annotate(msg, self._clock())
