"""Ingress Loop: SDK event entry point.

Bridges the **synchronous SDK boundary** (or FastAPI handlers) into the
asynchronous actor constellation. Responsible for:

* Validating the inbound event schema (delegated to pydantic).
* Creating a per-request ``asyncio.Future`` so callers can await a
  ``Decision``.
* Publishing the event onto the ``tool_call_requested`` topic so
  :class:`SessionActor` picks it up.
* Cancelling outstanding futures with a clear ``RuntimeError`` on
  shutdown so blocked callers don't leak.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class IngressLoop:
    """Producer side of the actor pipeline."""

    def __init__(self, bus: EventBus, *, default_timeout_s: float = 30.0) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[tuple[RuntimeEvent, asyncio.Future[Any]]] = asyncio.Queue()
        self._default_timeout = default_timeout_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Future[Any]] = set()
        self._submitted = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="ingress-loop")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Cancel any callers still waiting on a Decision.
        for fut in list(self._inflight):
            if not fut.done():
                fut.set_exception(RuntimeError("ingress shutting down"))
        self._inflight.clear()

    @property
    def submitted(self) -> int:
        return self._submitted

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    async def submit(
        self,
        event: RuntimeEvent,
        *,
        timeout_s: float | None = None,
    ) -> Decision:
        """Submit an event and wait for a :class:`Decision`.

        Raises ``asyncio.TimeoutError`` if no decision is produced within
        ``timeout_s`` (defaults to ``default_timeout_s``).
        """
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._inflight.add(future)
        future.add_done_callback(self._inflight.discard)

        await self._queue.put((event, future))
        self._submitted += 1

        try:
            return await asyncio.wait_for(
                future, timeout=timeout_s or self._default_timeout
            )
        except asyncio.TimeoutError:
            log.warning("ingress decision timed out: event_id=%s", event.event_id)
            raise

    async def _run(self) -> None:
        while self._running:
            try:
                event, future = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if future.done():
                # Caller already gave up (timeout / cancel). Skip.
                continue

            msg = Message(
                topic="tool_call_requested",
                payload={"event": event},
                reply_to=future,
                sender="ingress",
            )
            try:
                await self._bus.publish(msg)
            except Exception as exc:
                log.error("ingress publish failed: %s", exc, exc_info=True)
                if not future.done():
                    future.set_exception(exc)
