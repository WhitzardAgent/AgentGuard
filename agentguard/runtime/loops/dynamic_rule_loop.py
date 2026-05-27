"""Dynamic-rule synthesis loop.

The :class:`DynamicRuleActor` listens to the ``slow_path_event`` topic and
forwards events to the :class:`SlowDispatcher`. This loop adds:

  * Risk-threshold filtering before paying the LLM-call cost.
  * Per-(agent, tool) cooldown so a single misbehaving agent cannot melt
    the synthesizer endpoint.
  * Cumulative metrics for ``/audit/recent`` style introspection.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from typing import Any

from agentguard.models.decisions import Action, Decision
from agentguard.models.events import RuntimeEvent
from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class DynamicRuleLoop:
    """Filtered bridge from ``slow_path_event`` to actual synthesis."""

    def __init__(
        self,
        bus: EventBus,
        *,
        risk_threshold: float = 0.6,
        cooldown_s: float = 10.0,
    ) -> None:
        self._bus = bus
        self._risk_threshold = risk_threshold
        self._cooldown_s = cooldown_s
        self._lock = threading.Lock()
        self._last_fire: dict[str, float] = defaultdict(float)
        self._fired = 0
        self._suppressed_cooldown = 0
        self._suppressed_threshold = 0
        self._fire_reasons: Counter[str] = Counter()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._bus.subscribe("slow_path_event", self._handle)
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._bus.unsubscribe("slow_path_event", self._handle)
        self._running = False

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "fired": self._fired,
                "suppressed_threshold": self._suppressed_threshold,
                "suppressed_cooldown": self._suppressed_cooldown,
                "by_reason": dict(self._fire_reasons),
            }

    async def _handle(self, msg: Message) -> None:
        if not isinstance(msg.payload, dict):
            return
        event: RuntimeEvent | None = msg.payload.get("event")
        decision: Decision | None = msg.payload.get("decision")
        if event is None or decision is None:
            return

        if not self._should_fire(decision):
            with self._lock:
                self._suppressed_threshold += 1
            return

        bucket = self._bucket_key(event)
        now = time.time()
        with self._lock:
            last = self._last_fire[bucket]
            if now - last < self._cooldown_s:
                self._suppressed_cooldown += 1
                return
            self._last_fire[bucket] = now
            self._fired += 1
            self._fire_reasons[decision.action.value if isinstance(decision.action, Action)
                               else str(decision.action)] += 1

        # Re-emit on a private topic that DynamicRuleActor consumes; this
        # keeps the actor passive (it just forwards filtered events).
        await self._bus.publish(Message(
            topic="slow_path_filtered",
            payload={"event": event, "decision": decision},
            sender="dynamic_rule_loop",
        ))

    def _should_fire(self, decision: Decision) -> bool:
        if decision.risk_score >= self._risk_threshold:
            return True
        action = decision.action
        action_value = action.value if isinstance(action, Action) else str(action)
        return action_value in {"deny", "human_check"}

    @staticmethod
    def _bucket_key(event: RuntimeEvent) -> str:
        tool = event.tool_call.tool_name if event.tool_call else "?"
        return f"{event.principal.agent_id}:{tool}"
