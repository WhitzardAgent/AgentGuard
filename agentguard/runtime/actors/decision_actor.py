"""DecisionActor: final decision aggregation (Instruction.md §3.3).

Receives the policy-evaluated outcome from PolicyActor and:
  1. Synchronously appends the attempt to the chronological trace log so
     the next call's ``trace()`` predicate sees it.
  2. Replies to the ingress future so the caller unblocks.
  3. Fans out follow-up topics (degrade / human review / audit / graph /
     slow-path synthesis) to the corresponding actors.
"""

from __future__ import annotations

import logging

from agentguard.models.decisions import Action, Decision
from agentguard.models.events import EventType, RuntimeEvent
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.enrichment import append_trace
from agentguard.runtime.event_bus import EventBus, Message
from agentguard.storage.session_store import StateCache

log = logging.getLogger(__name__)


class DecisionActor(BaseActor):
    actor_name = "decision"

    def __init__(
        self,
        bus: EventBus,
        *,
        cache: StateCache | None = None,
        mode: str = "enforce",
    ) -> None:
        super().__init__(bus)
        self._cache = cache
        self.mode = mode

    async def handle(self, msg: Message) -> None:
        if msg.topic != "make_decision":
            return
        event: RuntimeEvent = msg.payload["event"]
        decision: Decision = msg.payload["decision"]

        # 1. Synchronously record the attempt in trace_log (before we even
        #    reply, so a sibling caller polling the cache always sees the
        #    decision history grow monotonically).
        if (
            self._cache is not None
            and event.tool_call is not None
            and event.event_type in (
                EventType.TOOL_CALL_ATTEMPT,
                EventType.TOOL_CALL_REQUESTED,
            )
        ):
            try:
                append_trace(event, self._cache)
            except Exception as exc:
                log.warning("trace append failed: %s", exc)

        # 2. Unblock the ingress future.
        self.reply(msg, decision)

        # 3. Fire-and-forget follow-up topics. monitor / dry_run modes
        #    still emit audit + graph so observability stays consistent.
        if decision.action is Action.DEGRADE:
            await self.bus.publish(Message(
                topic="degrade_request",
                payload={"event": event, "decision": decision},
                sender=self.actor_name,
            ))

        if decision.action is Action.HUMAN_CHECK:
            await self.bus.publish(Message(
                topic="human_review_request",
                payload={"event": event, "decision": decision},
                sender=self.actor_name,
            ))

        await self.bus.publish(Message(
            topic="audit_event",
            payload={"event": event, "decision": decision},
            sender=self.actor_name,
        ))

        await self.bus.publish(Message(
            topic="graph_update",
            payload={"event": event, "decision": decision},
            sender=self.actor_name,
        ))

        # 4. Always feed the slow-path stream — DynamicRuleActor decides
        #    whether to actually trigger an LLM synthesis based on its own
        #    risk thresholds and cooldowns.
        await self.bus.publish(Message(
            topic="slow_path_event",
            payload={"event": event, "decision": decision},
            sender=self.actor_name,
        ))

    async def on_start(self) -> None:
        self.bus.subscribe("make_decision", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("make_decision", self.receive)
