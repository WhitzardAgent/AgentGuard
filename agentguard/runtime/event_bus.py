"""Event Bus: asyncio-based pub/sub for inter-actor messaging.

Actors subscribe to event topics. The bus routes incoming messages to all
subscribers of the matching topic. Supports both async dispatch (fire-and-forget)
and request/reply patterns via asyncio.Future.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

log = logging.getLogger(__name__)

Topic = str
Handler = Callable[["Message"], Awaitable[None]]


@dataclass
class Message:
    """Envelope for inter-actor communication."""

    topic: Topic
    payload: Any
    reply_to: asyncio.Future[Any] | None = None
    sender: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """In-process pub/sub event bus backed by asyncio.Queue per subscriber."""

    def __init__(self) -> None:
        self._handlers: dict[Topic, list[Handler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: Topic, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    def unsubscribe(self, topic: Topic, handler: Handler) -> None:
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, message: Message) -> None:
        """Dispatch message to all handlers subscribed to the topic."""
        handlers = self._handlers.get(message.topic, [])
        for h in handlers:
            try:
                await h(message)
            except Exception as e:
                log.error("handler error on topic=%s: %s", message.topic, e)

    async def request(self, message: Message, timeout: float = 30.0) -> Any:
        """Publish and wait for reply (request/reply pattern)."""
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        message.reply_to = future
        await self.publish(message)
        return await asyncio.wait_for(future, timeout=timeout)

    def publish_nowait(self, message: Message) -> None:
        """Fire-and-forget publish from sync context."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self.publish(message))
        else:
            loop.run_until_complete(self.publish(message))
