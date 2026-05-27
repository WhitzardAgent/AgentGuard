"""HumanReviewActor: human-in-the-loop approval (Instruction.md §3.6).

Creates approval tickets when a decision requires human review. Ticket
*resolution* (auto-deny on timeout) is handled by :class:`ReviewLoop`.
"""

from __future__ import annotations

import logging
from typing import Any

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class HumanReviewActor(BaseActor):
    actor_name = "human_review"

    def __init__(self, bus: EventBus, approval_bridge: Any) -> None:
        super().__init__(bus)
        self._bridge = approval_bridge

    async def handle(self, msg: Message) -> None:
        if msg.topic != "human_review_request":
            return
        if not isinstance(msg.payload, dict):
            return
        event: RuntimeEvent | None = msg.payload.get("event")
        decision: Decision | None = msg.payload.get("decision")
        if event is None or decision is None:
            return
        ticket = self._bridge.enqueue(
            event_dump=event.model_dump(mode="json"),
            decision_dump=decision.model_dump(mode="json"),
        )
        log.info(
            "human review ticket created: %s for tool=%s",
            ticket.ticket_id,
            event.tool_call.tool_name if event.tool_call else "?",
        )

    async def on_start(self) -> None:
        self.bus.subscribe("human_review_request", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("human_review_request", self.receive)
