"""A tiny fluent DSL for building :class:`Rule` objects.

Example
-------
    from agentguard.policies import when
    from agentguard.schemas import EventType, DecisionAction

    rule = (
        when("block_rm", EventType.TOOL_CALL)
        .where(lambda e, c: e.tool_name == "shell" and "rm -rf" in str(e.args))
        .deny("destructive shell command")
    )

Rules can also be parsed from plain dicts (e.g. loaded from JSON/YAML) via
:func:`rule_from_dict`, allowing config-driven policies without code.
"""

from __future__ import annotations

from typing import Any, Iterable

from agentguard.policies.rule import Predicate, Rule
from agentguard.schemas.decision import DecisionAction, Obligation
from agentguard.schemas.events import EventType


class RuleBuilder:
    def __init__(self, rule_id: str, *event_types: EventType) -> None:
        self._id = rule_id
        self._event_types = frozenset(event_types) if event_types else None
        self._predicate: Predicate = lambda e, c: True
        self._priority = 100
        self._risk = 0.0
        self._obligations: list[Obligation] = []
        self._tags: list[str] = []

    def where(self, predicate: Predicate) -> "RuleBuilder":
        self._predicate = predicate
        return self

    def priority(self, value: int) -> "RuleBuilder":
        self._priority = value
        return self

    def risk(self, value: float) -> "RuleBuilder":
        self._risk = value
        return self

    def tag(self, *tags: str) -> "RuleBuilder":
        self._tags.extend(tags)
        return self

    def obligation(self, kind: str, **params: Any) -> "RuleBuilder":
        self._obligations.append(Obligation(kind=kind, params=params))
        return self

    def _build(self, action: DecisionAction, reason: str) -> Rule:
        return Rule(
            rule_id=self._id,
            action=action,
            predicate=self._predicate,
            event_types=self._event_types,
            reason=reason,
            priority=self._priority,
            risk_score=self._risk,
            obligations=list(self._obligations),
            tags=list(self._tags),
        )

    # ── terminal actions ────────────────────────────────────────────────
    def allow(self, reason: str = "") -> Rule:
        return self._build(DecisionAction.ALLOW, reason)

    def deny(self, reason: str = "") -> Rule:
        if self._risk == 0.0:
            self._risk = 1.0
        return self._build(DecisionAction.DENY, reason)

    def degrade(self, reason: str = "") -> Rule:
        return self._build(DecisionAction.DEGRADE, reason)

    def ask_user(self, reason: str = "") -> Rule:
        return self._build(DecisionAction.ASK_USER, reason)

    def sanitize(self, reason: str = "") -> Rule:
        return self._build(DecisionAction.SANITIZE, reason)

    def log_only(self, reason: str = "") -> Rule:
        return self._build(DecisionAction.LOG_ONLY, reason)

    def require_approval(self, reason: str = "") -> Rule:
        return self._build(DecisionAction.REQUIRE_APPROVAL, reason)


def when(rule_id: str, *event_types: EventType) -> RuleBuilder:
    """Entry point for the fluent rule DSL."""
    return RuleBuilder(rule_id, *event_types)


def rule_from_dict(spec: dict[str, Any]) -> Rule:
    """Build a rule from a config dict.

    Supported config-driven predicates (no arbitrary code):
      * ``tool_name``: exact tool name match
      * ``contains``: substring present in args+content (case-insensitive)
      * ``capabilities``: any of these capabilities present on the event
    """
    rule_id = str(spec["id"])
    action = DecisionAction(str(spec.get("action", "allow")))
    reason = str(spec.get("reason", ""))
    event_types = [EventType(t) for t in spec.get("event_types", [])]

    tool_name = spec.get("tool_name")
    contains = [s.lower() for s in spec.get("contains", [])]
    caps: Iterable[str] = spec.get("capabilities", [])

    def predicate(event: Any, _ctx: Any) -> bool:
        if tool_name is not None and event.tool_name != tool_name:
            return False
        if caps and not (set(caps) & set(event.capabilities)):
            return False
        if contains:
            haystack = f"{event.content or ''} {event.args}".lower()
            if not any(token in haystack for token in contains):
                return False
        return True

    builder = RuleBuilder(rule_id, *event_types).where(predicate)
    builder.priority(int(spec.get("priority", 100)))
    builder.risk(float(spec.get("risk_score", 0.0)))
    for ob in spec.get("obligations", []):
        builder.obligation(ob["kind"], **ob.get("params", {}))
    return builder._build(action, reason)
