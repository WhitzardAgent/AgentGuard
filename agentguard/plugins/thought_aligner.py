"""Thought-Aligner plugin.

Demonstrates a plugin that extends the Harness in three ways at once:

1. registers a **middleware** that detects goal-drift in LLM thoughts,
2. adds an **enforcement rule** that asks the user when drift is detected, and
3. subscribes a **lifecycle/event hook** to count aligned vs. drifting thoughts.

Load it dynamically::

    guard.load_plugin("agentguard.plugins.thought_aligner")
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agentguard.middleware.base import Middleware
from agentguard.plugins.manager import Plugin
from agentguard.policies.dsl import when
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.schemas.risk import RiskAssessment

if TYPE_CHECKING:
    from agentguard.facade import AgentGuard

_STOPWORDS = {"the", "a", "an", "to", "of", "and", "or", "for", "in", "on", "is", "with"}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z]{3,}", text.lower()) if w not in _STOPWORDS}


class GoalAlignmentMiddleware(Middleware):
    name = "thought_aligner"

    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        if event.type is not EventType.LLM_THOUGHT or not context.goal:
            return event
        goal_kw = _keywords(context.goal)
        thought_kw = _keywords(event.content or "")
        if not goal_kw:
            return event
        overlap = len(goal_kw & thought_kw) / max(1, len(goal_kw))
        event.annotate("goal_overlap", round(overlap, 2))
        if overlap < 0.15:
            event.annotate("goal_drift", True)
            risk.add("goal_drift", 0.5, overlap=round(overlap, 2))
        return event


class ThoughtAlignerPlugin(Plugin):
    name = "thought_aligner"

    def register(self, guard: "AgentGuard") -> None:
        guard.register_middleware(GoalAlignmentMiddleware())
        guard.add_rule(
            when("plugin.goal_drift", EventType.LLM_THOUGHT)
            .where(lambda e, c: bool(e.annotations.get("goal_drift")))
            .priority(40)
            .risk(0.5)
            .ask_user("reasoning appears to drift from the stated goal")
        )

        counters = {"aligned": 0, "drift": 0}

        def _count(event: RuntimeEvent) -> None:
            if event.type is EventType.LLM_THOUGHT:
                key = "drift" if event.annotations.get("goal_drift") else "aligned"
                counters[key] += 1

        guard.subscribe(EventType.LLM_THOUGHT, _count)
        guard.metadata["thought_aligner_counters"] = counters


# Module-level hook so the manager can load this via `register(guard)` too.
def register(guard: "AgentGuard") -> None:
    ThoughtAlignerPlugin().register(guard)
