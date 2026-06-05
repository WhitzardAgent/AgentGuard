"""Synchronous in-process event bus for normalized runtime events."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

from agentguard.schemas.events import EventType, RuntimeEvent

log = logging.getLogger("agentguard.harness")

Handler = Callable[[RuntimeEvent], None]
_WILDCARD = "*"


class EventBus:
    """Pub/sub for :class:`RuntimeEvent`. Handlers are called synchronously.

    Subscribe to a specific :class:`EventType` or to ``"*"`` for every event.
    Handler exceptions are logged and swallowed so one bad subscriber cannot
    break the enforcement path.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: EventType | str, handler: Handler) -> Callable[[], None]:
        key = event_type.value if isinstance(event_type, EventType) else event_type
        self._subscribers[key].append(handler)

        def unsubscribe() -> None:
            try:
                self._subscribers[key].remove(handler)
            except ValueError:
                pass

        return unsubscribe

    def publish(self, event: RuntimeEvent) -> None:
        for key in (event.type.value, _WILDCARD):
            for handler in list(self._subscribers.get(key, [])):
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001
                    log.warning("event handler failed for %s: %s", key, exc)
