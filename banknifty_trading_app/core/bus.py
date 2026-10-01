"""A tiny async pub/sub bus.

Design constraints:
  * Ticks are high frequency. A slow subscriber must never stall the feed, so
    tick queues drop the *oldest* item when full (last-value-wins semantics).
  * Order/fill events are never dropped; if those queues fill up that is a
    genuine fault and we log loudly.
  * The feed runs on a WebSocket thread, so ``publish_threadsafe`` marshals the
    call back onto the asyncio loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any

from .events import DROPPABLE_TOPICS
from ..logging import get_logger

log = get_logger("core.bus")


class Subscription:
    def __init__(self, topic: str, queue: asyncio.Queue):
        self.topic = topic
        self.queue = queue
        self.dropped = 0

    async def get(self) -> Any:
        return await self.queue.get()


class EventBus:
    def __init__(self, default_maxsize: int = 10_000) -> None:
        self._subs: dict[str, list[Subscription]] = defaultdict(list)
        self._default_maxsize = default_maxsize
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, topic: str, maxsize: int | None = None) -> Subscription:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize or self._default_maxsize)
        sub = Subscription(topic, q)
        with self._lock:
            self._subs[topic].append(sub)
        return sub

    def publish(self, topic: str, event: Any) -> None:
        with self._lock:
            subs = list(self._subs.get(topic, ()))
        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                if topic in DROPPABLE_TOPICS:
                    # drop the oldest, keep the newest state
                    try:
                        sub.queue.get_nowait()
                        sub.queue.put_nowait(event)
                        sub.dropped += 1
                    except Exception:  # pragma: no cover - defensive
                        pass
                else:
                    log.error(
                        "Bus queue full on non-droppable topic=%s subscriber_dropped=%d",
                        topic,
                        sub.dropped,
                    )

    def publish_threadsafe(self, topic: str, event: Any) -> None:
        """Publish from a non-asyncio thread (the WebSocket reader)."""
        if self._loop is None:
            self.publish(topic, event)
            return
        try:
            self._loop.call_soon_threadsafe(self.publish, topic, event)
        except RuntimeError:  # loop closed during shutdown
            pass
