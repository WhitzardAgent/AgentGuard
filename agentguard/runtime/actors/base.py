"""BaseActor: asyncio mailbox-based actor abstraction.

Each actor owns a mailbox (asyncio.Queue), processes messages sequentially,
and communicates with other actors only through the EventBus.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class BaseActor:
    """Abstract base for all AgentGuard actors."""

    actor_name: str = "base"

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._mailbox: asyncio.Queue[Message] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the actor's message processing loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"actor-{self.actor_name}")
        await self.on_start()

    async def stop(self) -> None:
        """Gracefully stop the actor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.on_stop()

    async def _run_loop(self) -> None:
        """Main processing loop: dequeue and handle messages."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._mailbox.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await self.handle(msg)
            except Exception as e:
                log.error("[%s] handle error: %s", self.actor_name, e, exc_info=True)

    async def receive(self, msg: Message) -> None:
        """Put a message into this actor's mailbox (called by bus handler)."""
        await self._mailbox.put(msg)

    async def handle(self, msg: Message) -> None:
        """Override in subclass to process messages."""
        raise NotImplementedError

    async def on_start(self) -> None:
        """Hook called after actor starts. Override for initialization."""

    async def on_stop(self) -> None:
        """Hook called after actor stops. Override for cleanup."""

    def reply(self, msg: Message, result: Any) -> None:
        """Reply to a request/reply message."""
        if msg.reply_to and not msg.reply_to.done():
            msg.reply_to.set_result(result)

    def reply_error(self, msg: Message, error: Exception) -> None:
        if msg.reply_to and not msg.reply_to.done():
            msg.reply_to.set_exception(error)
