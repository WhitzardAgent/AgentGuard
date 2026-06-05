"""Policy rule definition for the client-side PEP.

A rule is a predicate over ``(event, context)`` plus the decision to emit when
it matches. Predicates are plain Python callables which keeps the matcher fast
and lets plugins contribute rules without a parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import DecisionAction, Obligation
from agentguard.schemas.events import EventType, RuntimeEvent

Predicate = Callable[[RuntimeEvent, RuntimeContext], bool]


@dataclass
class Rule:
    rule_id: str
    action: DecisionAction
    predicate: Predicate
    event_types: frozenset[EventType] | None = None
    reason: str = ""
    priority: int = 100
    risk_score: float = 0.0
    obligations: list[Obligation] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def matches(self, event: RuntimeEvent, context: RuntimeContext) -> bool:
        if self.event_types is not None and event.type not in self.event_types:
            return False
        try:
            return bool(self.predicate(event, context))
        except Exception:
            # A faulty predicate must never crash enforcement.
            return False
