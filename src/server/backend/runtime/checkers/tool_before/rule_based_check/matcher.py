"""Local rule matching helpers for the optional rule-based checker."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from shared.schemas.decisions import DecisionType
from shared.schemas.events import RuntimeEvent


_EFFECT_RANK = {
    "deny": 7,
    "require_remote_review": 6,
    "require_approval": 5,
    "degrade": 4,
    "sanitize": 3,
    "log_only": 2,
    "allow": 1,
}

_EFFECT_TO_DECISION = {
    "allow": DecisionType.ALLOW,
    "deny": DecisionType.DENY,
    "sanitize": DecisionType.SANITIZE,
    "degrade": DecisionType.DEGRADE,
    "require_approval": DecisionType.REQUIRE_APPROVAL,
    "require_remote_review": DecisionType.REQUIRE_REMOTE_REVIEW,
    "log_only": DecisionType.LOG_ONLY,
}


@dataclass
class RuleMatch:
    matched: bool
    rule: Any | None = None
    effect: str | None = None
    reason: str = ""
    all_matched: list[Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "rule_id": getattr(self.rule, "rule_id", None) if self.rule else None,
            "effect": self.effect,
            "reason": self.reason,
            "matched_rule_ids": [
                getattr(rule, "rule_id", None) for rule in (self.all_matched or [])
            ],
        }


def match_rules(
    rules: list[Any],
    event: RuntimeEvent,
    trace_window: list[RuntimeEvent] | None = None,
) -> RuleMatch:
    matched = [rule for rule in rules if _rule_matches(rule, event, trace_window)]
    if not matched:
        return RuleMatch(matched=False, all_matched=[])

    def sort_key(rule: Any) -> tuple[int, int]:
        return (int(getattr(rule, "priority", 0) or 0), _EFFECT_RANK.get(_effect_value(rule), 0))

    winner = max(matched, key=sort_key)
    return RuleMatch(
        matched=True,
        rule=winner,
        effect=_effect_value(winner),
        reason=str(getattr(winner, "reason", "") or ""),
        all_matched=matched,
    )


def effect_to_decision(effect: str) -> DecisionType:
    return _EFFECT_TO_DECISION[effect]


def _rule_matches(
    rule: Any,
    event: RuntimeEvent,
    trace_window: list[RuntimeEvent] | None,
) -> bool:
    matches = getattr(rule, "matches", None)
    if callable(matches):
        return bool(matches(event, trace_window))
    return False


def _effect_value(rule: Any) -> str:
    effect = getattr(rule, "effect", "")
    if isinstance(effect, Enum):
        return str(effect.value)
    return str(effect)
