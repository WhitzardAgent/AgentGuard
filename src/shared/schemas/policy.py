"""Policy rule schema, condition matching and effect mapping."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shared.schemas.decisions import DecisionType
from shared.schemas.events import RuntimeEvent


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    SANITIZE = "sanitize"
    DEGRADE = "degrade"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_REMOTE_REVIEW = "require_remote_review"
    LOG_ONLY = "log_only"


_EFFECT_TO_DECISION = {
    PolicyEffect.ALLOW: DecisionType.ALLOW,
    PolicyEffect.DENY: DecisionType.DENY,
    PolicyEffect.SANITIZE: DecisionType.SANITIZE,
    PolicyEffect.DEGRADE: DecisionType.DEGRADE,
    PolicyEffect.REQUIRE_APPROVAL: DecisionType.REQUIRE_APPROVAL,
    PolicyEffect.REQUIRE_REMOTE_REVIEW: DecisionType.REQUIRE_REMOTE_REVIEW,
    PolicyEffect.LOG_ONLY: DecisionType.LOG_ONLY,
}


def effect_to_decision(effect: PolicyEffect) -> DecisionType:
    return _EFFECT_TO_DECISION[effect]


@dataclass
class RuleCondition:
    """A single field predicate. `field` is a dotted path into the event dict.

    Special prefixes:
      trace.contains_event_type / trace.contains_signal -> trace-window predicates
    """

    field: str
    op: str = "eq"
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleCondition":
        return cls(field=data["field"], op=data.get("op", "eq"), value=data.get("value"))


def _resolve(path: str, root: dict[str, Any]) -> Any:
    cur: Any = root
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _apply_op(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op == "contains":
        return expected in actual if actual is not None else False
    if op == "icontains":
        return str(expected).lower() in str(actual or "").lower()
    if op == "any_in":
        a = set(actual or []) if isinstance(actual, (list, set, tuple)) else {actual}
        return bool(a & set(expected or []))
    if op == "regex":
        return bool(re.search(str(expected), str(actual or "")))
    if op == "exists":
        return (actual is not None) == bool(expected)
    if op == "gt":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lt":
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    return False


@dataclass
class PolicyRule:
    rule_id: str
    effect: PolicyEffect
    reason: str = ""
    priority: int = 0
    event_types: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    conditions: list[RuleCondition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "effect": self.effect.value,
            "reason": self.reason,
            "priority": self.priority,
            "event_types": list(self.event_types),
            "tool_names": list(self.tool_names),
            "capabilities": list(self.capabilities),
            "risk_signals": list(self.risk_signals),
            "conditions": [c.to_dict() for c in self.conditions],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyRule":
        return cls(
            rule_id=data["rule_id"],
            effect=PolicyEffect(data["effect"]),
            reason=data.get("reason", ""),
            priority=int(data.get("priority", 0)),
            event_types=list(data.get("event_types") or []),
            tool_names=list(data.get("tool_names") or []),
            capabilities=list(data.get("capabilities") or []),
            risk_signals=list(data.get("risk_signals") or []),
            conditions=[RuleCondition.from_dict(c) for c in data.get("conditions") or []],
            metadata=dict(data.get("metadata") or {}),
        )

    # ---- matching ------------------------------------------------------
    def matches(
        self,
        event: RuntimeEvent,
        trace_window: list[RuntimeEvent] | None = None,
    ) -> bool:
        if self.event_types and event.event_type.value not in self.event_types:
            return False

        if self.tool_names:
            tool = getattr(event.payload, "tool_name", None)
            if not _wildcard_match(tool, self.tool_names):
                return False

        if self.capabilities:
            caps = set(getattr(event.payload, "capabilities", []) or [])
            if not (caps & set(self.capabilities)):
                return False

        if self.risk_signals:
            if not (set(event.risk_signals) & set(self.risk_signals)):
                return False

        event_dict = event.to_dict()
        for cond in self.conditions:
            if cond.field.startswith("trace."):
                if not _match_trace(cond, trace_window or []):
                    return False
                continue
            actual = _resolve(cond.field, event_dict)
            if not _apply_op(cond.op, actual, cond.value):
                return False
        return True


def _wildcard_match(value: Any, patterns: list[str]) -> bool:
    if value is None:
        return False
    for p in patterns:
        if p == "*" or p == value:
            return True
        if p.endswith("*") and str(value).startswith(p[:-1]):
            return True
    return False


def _match_trace(cond: RuleCondition, window: list[RuntimeEvent]) -> bool:
    key = cond.field.split(".", 1)[1]
    if key == "contains_event_type":
        return any(e.event_type.value == cond.value for e in window)
    if key == "contains_signal":
        return any(cond.value in e.risk_signals for e in window)
    if key == "sequence":
        # value is an ordered list of event_type strings to appear in order.
        wanted = list(cond.value or [])
        idx = 0
        for e in window:
            if idx < len(wanted) and e.event_type.value == wanted[idx]:
                idx += 1
        return idx >= len(wanted)
    return False
