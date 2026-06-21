"""Server-side policy snapshot: versioned rule set with indexes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.rules.builtin import builtin_rules
from shared.rules.matcher import MatchResult, match_rules
from shared.schemas.events import RuntimeEvent
from shared.schemas.policy import PolicyRule
from shared.utils.hash import stable_hash


@dataclass
class PolicySnapshot:
    """Immutable-ish compiled policy snapshot sent to clients."""

    version: str = "v0"
    rules: list[PolicyRule] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    _by_capability: dict[str, list[PolicyRule]] = field(default_factory=dict, repr=False)
    _by_risk: dict[str, list[PolicyRule]] = field(default_factory=dict, repr=False)
    _by_event: dict[str, list[PolicyRule]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._build_indexes()

    def _build_indexes(self) -> None:
        self._by_capability = {}
        self._by_risk = {}
        self._by_event = {}
        for rule in self.rules:
            for capability in rule.capabilities:
                self._by_capability.setdefault(capability, []).append(rule)
            for signal in rule.risk_signals:
                self._by_risk.setdefault(signal, []).append(rule)
            for event_type in rule.event_types:
                self._by_event.setdefault(event_type, []).append(rule)

    def evaluate(
        self, event: RuntimeEvent, trace_window: list[RuntimeEvent] | None = None
    ) -> MatchResult:
        return match_rules(self.rules, event, trace_window)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rules": [rule.to_dict() for rule in self.rules],
            "metadata": self.metadata,
            "stable_hash": self.stable_hash(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicySnapshot":
        return cls(
            version=data.get("version", "v0"),
            rules=[PolicyRule.from_dict(rule) for rule in data.get("rules") or []],
            metadata=dict(data.get("metadata") or {}),
        )

    def stable_hash(self) -> str:
        return stable_hash(
            {"version": self.version, "rules": [rule.to_dict() for rule in self.rules]}
        )

    @classmethod
    def default(cls) -> "PolicySnapshot":
        return cls(version="builtin", rules=builtin_rules())
