"""Rule persistence facade — delegates to RuleRegistry for in-memory MVP."""

from __future__ import annotations

from typing import Iterable

from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.policy.rules.registry import RuleRegistry


class RuleStore:
    """Thin persistence wrapper. Swap internals for DB-backed storage later."""

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        self._registry = registry or RuleRegistry()

    @property
    def registry(self) -> RuleRegistry:
        return self._registry

    def replace(self, rules: Iterable[CompiledRule]) -> int:
        return self._registry.replace(rules)

    def upsert(self, rule: CompiledRule) -> int:
        return self._registry.upsert(rule)

    def remove(self, rule_id: str) -> bool:
        return self._registry.remove(rule_id)

    def active(self) -> list[CompiledRule]:
        return self._registry.active()
