"""Rule matching with priority and deny-overrides resolution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.policy import PolicyEffect, PolicyRule

# Effect precedence when priorities tie (higher = stronger).
_EFFECT_RANK = {
    PolicyEffect.DENY: 7,
    PolicyEffect.REQUIRE_REMOTE_REVIEW: 6,
    PolicyEffect.REQUIRE_APPROVAL: 5,
    PolicyEffect.DEGRADE: 4,
    PolicyEffect.SANITIZE: 3,
    PolicyEffect.LOG_ONLY: 2,
    PolicyEffect.ALLOW: 1,
}


@dataclass
class MatchResult:
    matched: bool
    rule: PolicyRule | None = None
    effect: PolicyEffect | None = None
    reason: str = ""
    all_matched: list[PolicyRule] = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "rule_id": self.rule.rule_id if self.rule else None,
            "effect": self.effect.value if self.effect else None,
            "reason": self.reason,
            "matched_rule_ids": [r.rule_id for r in (self.all_matched or [])],
        }


def match_rules(
    rules: list[PolicyRule],
    event: RuntimeEvent,
    trace_window: list[RuntimeEvent] | None = None,
) -> MatchResult:
    """Return the winning rule using priority then deny-overrides."""
    matched = [r for r in rules if r.matches(event, trace_window)]
    if not matched:
        return MatchResult(matched=False, all_matched=[])

    def sort_key(r: PolicyRule) -> tuple[int, int]:
        return (r.priority, _EFFECT_RANK.get(r.effect, 0))

    winner = max(matched, key=sort_key)
    return MatchResult(
        matched=True,
        rule=winner,
        effect=winner.effect,
        reason=winner.reason,
        all_matched=matched,
    )
