"""In-memory EventBus (interfaces.EventBus). A Redis implementation can replace it without touching callers."""
import asyncio
from collections import defaultdict

from pydantic import BaseModel

_CLOSED = object()


class Subscription:
    """Registered the moment subscribe() is called (not on first iteration), so no message is missed."""

    def __init__(self, bus: "InMemoryEventBus", channel: str):
        """Bind the subscription to its bus and channel and create its message queue."""
        self._bus, self.channel, self._queue = bus, channel, asyncio.Queue()

    def __aiter__(self):
        """A subscription is its own async iterator (use it with `async for`)."""
        return self

    async def __anext__(self) -> dict:
        """Wait for the next message; ends the loop once the subscription is closed."""
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item

    def deliver(self, payload: dict) -> None:
        """Called by the bus: put a published payload on this subscription's queue."""
        self._queue.put_nowait(payload)

    def close(self) -> None:
        """Unregister from the bus and wake any waiting reader so its loop ends."""
        self._bus._drop(self)
        self._queue.put_nowait(_CLOSED)


class InMemoryEventBus:
    """Single-process pub/sub keyed by channel name (ot.telemetry.*, ml.health.*, incident.*.events ...)."""

    def __init__(self):
        """Start with no subscribers."""
        self._subs: dict[str, set[Subscription]] = defaultdict(set)

    async def publish(self, channel: str, message: BaseModel) -> None:
        """Convert the message to JSON-able data and hand it to every subscriber of the channel."""
        payload = message.model_dump(mode="json")
        for sub in list(self._subs.get(channel, ())):
            sub.deliver(payload)

    def subscribe(self, channel: str) -> Subscription:
        """Register a new subscriber right away and return it as an async iterator."""
        sub = Subscription(self, channel)
        self._subs[channel].add(sub)
        return sub

    def _drop(self, sub: Subscription) -> None:
        """Remove a closed subscription from its channel."""
        self._subs[sub.channel].discard(sub)
