"""Async event bus with bounded backpressure and supervised handlers."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)
Handler = Callable[["Event"], Awaitable[None]]


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]


class EventEngine:
    def __init__(self, max_queue_size: int = 1000) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._active = False
        self._worker: asyncio.Task | None = None
        self._handler_tasks: set[asyncio.Task] = set()

    def register(self, event_type: str, handler: Handler) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unregister(self, event_type: str, handler: Handler) -> None:
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def put(self, event: Event) -> bool:
        if not self._active:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            log.error("event queue full; dropping event=%s", event.type)
            return False

    async def _run(self) -> None:
        while self._active:
            event = await self._queue.get()
            try:
                for handler in self._handlers.get(event.type, []):
                    task = asyncio.create_task(handler(event))
                    self._handler_tasks.add(task)
                    task.add_done_callback(self._handler_tasks.discard)
                    task.add_done_callback(self._report_task)
            finally:
                self._queue.task_done()

    @staticmethod
    def _report_task(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.exception("event handler failed", exc_info=task.exception())

    def start(self) -> None:
        if not self._active:
            self._active = True
            self._worker = asyncio.create_task(self._run())

    async def stop_async(self) -> None:
        self._active = False
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        for task in list(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)
        self._handler_tasks.clear()

    def stop(self) -> None:
        self._active = False
        if self._worker:
            self._worker.cancel()


# Singleton retained for compatibility.
event_engine = EventEngine()
