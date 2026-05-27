"""SessionActor: per-session orchestrator (Instruction.md §3.1).

Receives SDK events, enriches context, computes fast features, and forwards
to PolicyActor for evaluation.

Both this actor and the synchronous :class:`Pipeline` share the enrichment
logic in :mod:`agentguard.runtime.enrichment`, so DSL predicates evaluate
identically in either runtime mode.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from agentguard.models.events import RuntimeEvent
from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.enrichment import compute_fast_features, enrich_event
from agentguard.runtime.event_bus import EventBus, Message
from agentguard.storage.graph_store import GraphReadAPI
from agentguard.storage.session_store import StateCache

log = logging.getLogger(__name__)


class SessionActor(BaseActor):
    """Orchestrator actor for a single agent session."""

    actor_name = "session"

    def __init__(
        self,
        bus: EventBus,
        cache: StateCache,
        graph: GraphReadAPI,
        *,
        rules: Iterable[CompiledRule] | None = None,
        allowlists: dict[str, Any] | None = None,
        router: Any = None,
    ) -> None:
        super().__init__(bus)
        self._cache = cache
        self._graph = graph
        self._allowlists = allowlists or {}
        self._rules: list[CompiledRule] = list(rules) if rules else []
        self._router = router

    def load_rules(self, rules: Iterable[CompiledRule]) -> None:
        self._rules = list(rules)

    def enrich(self, event: RuntimeEvent) -> RuntimeEvent:
        return enrich_event(event, self._cache)

    def fast_features(self, event: RuntimeEvent) -> dict[str, Any]:
        if self._router is not None:
            agent_id = event.principal.agent_id if event.principal else ""
            scoped = self._router.rules_for_agent(agent_id)
        else:
            scoped = self._rules
        return compute_fast_features(
            event,
            cache=self._cache,
            graph=self._graph,
            rules=scoped,
            allowlists=self._allowlists,
        )

    async def handle(self, msg: Message) -> None:
        if msg.topic != "tool_call_requested":
            return
        event: RuntimeEvent = msg.payload["event"]
        try:
            enriched = self.enrich(event)
            features = self.fast_features(enriched)
        except Exception as exc:
            log.error("[session] enrichment failed: %s", exc, exc_info=True)
            self.reply_error(msg, exc)
            return

        eval_msg = Message(
            topic="evaluate_policy",
            payload={"event": enriched, "features": features},
            reply_to=msg.reply_to,
            sender=self.actor_name,
        )
        await self.bus.publish(eval_msg)

    async def on_start(self) -> None:
        self.bus.subscribe("tool_call_requested", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("tool_call_requested", self.receive)
