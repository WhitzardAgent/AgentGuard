"""DynamicRuleActor: runtime rule synthesis (Instruction.md §3.5).

Receives risk-filtered events from :class:`DynamicRuleLoop` (topic
``slow_path_filtered``) and forwards them to the
:class:`SlowDispatcher`, which executes any registered LLM-synthesis
hooks asynchronously.
"""

from __future__ import annotations

import logging

from agentguard.models.events import RuntimeEvent
from agentguard.policy.rules.dynamic_store import SlowDispatcher
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class DynamicRuleActor(BaseActor):
    actor_name = "dynamic_rule"

    def __init__(self, bus: EventBus, slow_dispatcher: SlowDispatcher) -> None:
        super().__init__(bus)
        self._slow = slow_dispatcher

    async def handle(self, msg: Message) -> None:
        if msg.topic != "slow_path_filtered":
            return
        event: RuntimeEvent | None = (
            msg.payload.get("event") if isinstance(msg.payload, dict) else None
        )
        if event is None:
            return
        try:
            self._slow.submit(event)
        except Exception as exc:
            log.warning("slow dispatcher rejected event: %s", exc)

    async def on_start(self) -> None:
        self.bus.subscribe("slow_path_filtered", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("slow_path_filtered", self.receive)
        self._slow.close()
