"""Evaluates an event against a set of rules and produces one Decision."""

from __future__ import annotations

from agentguard.policies.rule import Rule
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision, DecisionAction
from agentguard.schemas.events import RuntimeEvent


class PolicyMatcher:
    """Holds the active rule set and resolves decisions.

    When several rules match, the one whose action has the highest precedence
    wins (``deny`` beats ``sanitize`` beats ``allow`` …); ties break by the
    rule's ``priority`` field (lower first).
    """

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = list(rules or [])

    def add(self, rule: Rule) -> None:
        self._rules.append(rule)

    def extend(self, rules: list[Rule]) -> None:
        self._rules.extend(rules)

    def replace(self, rules: list[Rule]) -> None:
        self._rules = list(rules)

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def evaluate(self, event: RuntimeEvent, context: RuntimeContext) -> Decision:
        matched = [r for r in self._rules if r.matches(event, context)]
        if not matched:
            return Decision.allow()

        # winner: best precedence, then lowest priority value
        matched.sort(key=lambda r: (r.action.precedence, r.priority))
        winner = matched[0]
        return Decision(
            action=winner.action,
            reason=winner.reason or f"matched:{winner.rule_id}",
            risk_score=max((r.risk_score for r in matched), default=winner.risk_score),
            matched_rules=[r.rule_id for r in matched],
            obligations=list(winner.obligations),
            source="local",
            metadata={"action_default": DecisionAction.ALLOW.value},
        )
