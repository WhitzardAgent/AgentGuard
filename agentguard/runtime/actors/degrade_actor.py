"""DegradeActor: degrade-profile bookkeeping (Instruction.md §3.7).

The actual ToolCall rewrite lives in
:class:`agentguard.degrade.transformers.ActionExecutor` and is applied
on the synchronous Enforcer side. This actor merely records that a
degrade was selected so /audit/recent can correlate the original tool
attempt with the rewritten one.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class DegradeActor(BaseActor):
    actor_name = "degrade"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)
        self._profile_counts: Counter[str] = Counter()
        self._total = 0

    async def handle(self, msg: Message) -> None:
        if msg.topic != "degrade_request":
            return
        if not isinstance(msg.payload, dict):
            return
        event: RuntimeEvent | None = msg.payload.get("event")
        decision: Decision | None = msg.payload.get("decision")
        if event is None or decision is None:
            return
        self._total += 1
        if decision.degrade_profile:
            self._profile_counts[decision.degrade_profile] += 1
        log.info(
            "degrade requested for tool=%s profile=%s",
            event.tool_call.tool_name if event.tool_call else "?",
            decision.degrade_profile,
        )

    def metrics(self) -> dict[str, Any]:
        return {
            "total": self._total,
            "by_profile": dict(self._profile_counts),
        }

    async def on_start(self) -> None:
        self.bus.subscribe("degrade_request", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("degrade_request", self.receive)
