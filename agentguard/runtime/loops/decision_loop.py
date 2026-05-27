"""Decision metrics aggregator.

Subscribes to ``make_decision`` on the EventBus and counts decisions by
action / risk bucket. Lightweight observability layer that complements
DecisionActor (which handles the actual decision routing).
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

from agentguard.models.decisions import Action, Decision
from agentguard.runtime.event_bus import EventBus, Message

log = logging.getLogger(__name__)


class DecisionLoop:
    """Counts decisions by action and tracks risk-score distribution."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._lock = threading.Lock()
        self._action_counts: Counter[str] = Counter()
        self._risk_buckets: Counter[str] = Counter()  # low/medium/high/critical
        self._matched_rules: Counter[str] = Counter()
        self._total: int = 0
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._bus.subscribe("make_decision", self._handle)
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._bus.unsubscribe("make_decision", self._handle)
        self._running = False

    async def _handle(self, msg: Message) -> None:
        decision: Decision | None = msg.payload.get("decision") if isinstance(msg.payload, dict) else None
        if decision is None:
            return
        with self._lock:
            self._total += 1
            self._action_counts[decision.action.value if isinstance(decision.action, Action)
                                else str(decision.action)] += 1
            self._risk_buckets[_risk_bucket(decision.risk_score)] += 1
            for rid in decision.matched_rules:
                self._matched_rules[rid] += 1

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": self._total,
                "by_action": dict(self._action_counts),
                "by_risk": dict(self._risk_buckets),
                "top_rules": self._matched_rules.most_common(10),
            }


def _risk_bucket(score: float) -> str:
    if score >= 0.9:
        return "critical"
    if score >= 0.6:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"
