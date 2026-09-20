"""In-process async event bus.

The ingest client publishes gateway events here; websocket hubs, the
realtime metrics engine and the timescale writer subscribe. Subscribers
may be sync or async callables receiving one envelope dict. A failing
subscriber is logged and skipped -- it must never break the fan-out.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Callable

from app.common.logging import get_logger

logger = get_logger("ingest.bus")

Subscriber = Callable[[dict], None]


class _Subscription:
    __slots__ = ("callback", "event_types", "queue", "task")

    def __init__(self, callback: Subscriber, event_types: frozenset | None):
        self.callback = callback
        self.event_types = event_types
        self.queue: asyncio.Queue | None = None
        self.task: asyncio.Task | None = None


class EventBus:
    def __init__(self, max_queue: int = 10_000):
        self._subs: list[_Subscription] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue
        self.dropped: int = 0
        self.published: int = 0

    async def subscribe(self, callback: Subscriber,
                        event_types: set[str] | None = None,
                        queued: bool = True) -> Callable[[], None]:
        """Register a subscriber; returns an unsubscribe callable.

        queued=True wraps the callback in a bounded queue + pump task so a
        slow consumer never blocks the publisher (events are dropped and
        counted when its queue overflows). queued=False calls the callback
        inline in publish order (for cheap, ordering-critical consumers).
        """
        sub = _Subscription(callback, frozenset(event_types) if event_types else None)
        if queued:
            sub.queue = asyncio.Queue(maxsize=self._max_queue)

            async def _pump() -> None:
                while True:
                    event = await sub.queue.get()
                    try:
                        await self._invoke(sub.callback, event)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.exception("bus subscriber failed")

            sub.task = asyncio.create_task(_pump(), name="bus-subscriber")
        async with self._lock:
            self._subs.append(sub)

        def unsubscribe() -> None:
            self._remove(sub)

        return unsubscribe

    def _remove(self, sub: _Subscription) -> None:
        try:
            self._subs.remove(sub)
        except ValueError:
            pass
        if sub.task is not None:
            sub.task.cancel()

    @staticmethod
    async def _invoke(callback: Subscriber, event: dict) -> None:
        result = callback(event)
        if inspect.isawaitable(result):
            await result

    async def publish(self, event: dict) -> None:
        """Fan out one event envelope."""
        self.published += 1
        for sub in list(self._subs):
            if sub.event_types is not None and event.get("type") not in sub.event_types:
                continue
            if sub.queue is not None:
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    self.dropped += 1
            else:
                await self._invoke(sub.callback, event)

    async def close(self) -> None:
        for sub in list(self._subs):
            self._remove(sub)
