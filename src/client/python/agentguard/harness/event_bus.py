"""Synchronous in-process event bus."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from agentguard.schemas.events import EventType, RuntimeEvent

Listener = Callable[[RuntimeEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[EventType | None, list[Listener]] = defaultdict(list)

    def subscribe(self, event_type: EventType | None, listener: Listener) -> None:
        """Subscribe to one event type, or None for all events."""
        self._listeners[event_type].append(listener)

    def publish(self, event: RuntimeEvent) -> None:
        for listener in list(self._listeners.get(event.event_type, [])):
            _safe_call(listener, event)
        for listener in list(self._listeners.get(None, [])):
            _safe_call(listener, event)


def _safe_call(listener: Listener, event: RuntimeEvent) -> None:
    try:
        listener(event)
    except Exception:  # listeners must never break the runtime
        pass
