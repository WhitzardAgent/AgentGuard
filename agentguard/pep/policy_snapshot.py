"""Immutable snapshot of the active rule set with a content version."""

from __future__ import annotations

from agentguard.policies.matcher import PolicyMatcher
from agentguard.policies.rule import Rule
from agentguard.utils.hash import stable_hash


class PolicySnapshot:
    """A versioned, point-in-time view of the policy rules.

    The ``version`` is derived from the rule ids + actions so two snapshots with
    identical logical content share a version (handy for cache invalidation).
    """

    def __init__(self, rules: list[Rule], *, policy_name: str = "default") -> None:
        self.policy_name = policy_name
        self._rules = list(rules)
        self.matcher = PolicyMatcher(self._rules)
        self.version = self._compute_version()

    def _compute_version(self) -> str:
        fingerprint = [
            {"id": r.rule_id, "action": r.action.value, "priority": r.priority}
            for r in self._rules
        ]
        return stable_hash({"policy": self.policy_name, "rules": fingerprint})

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def with_rules(self, extra: list[Rule]) -> "PolicySnapshot":
        return PolicySnapshot([*self._rules, *extra], policy_name=self.policy_name)
