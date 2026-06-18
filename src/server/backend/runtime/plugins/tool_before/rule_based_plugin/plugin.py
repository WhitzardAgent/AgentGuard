"""Rule-based plugin backed by the server policy rule store."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.policy import PolicyEffect, PolicyRule
from shared.tools.capability import CAP_EXTERNAL_SEND
from shared.schemas.events import RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from backend.runtime.plugins.tool_before.rule_based_plugin.matcher import (
    RuleMatch,
    effect_to_decision,
    match_rules,
)


@register(
    name="rule_based_plugin",
    description="Evaluate server policy rules against the current event and trajectory window.",
)
class RuleBasedPlugin(BasePlugin):
    """Evaluate PolicyRule objects and return the winning rule decision."""

    event_types = []

    def __init__(
        self,
        *,
        policy_store: Any | None = None,
        rules_provider: Callable[[], list[Any]] | None = None,
        policy_version_provider: Callable[[], str] | None = None,
    ) -> None:
        if policy_store is None:
            from backend.runtime.policy.store import PolicyStore  # noqa: PLC0415

            policy_store = PolicyStore.default()
        self._policy_store = policy_store
        self._rules_provider = rules_provider
        self._policy_version_provider = policy_version_provider

    def set_policy_store(self, policy_store: Any) -> None:
        self._policy_store = policy_store

    def attach_policy(self, policy: Any) -> None:
        store = getattr(policy, "store", None)
        if store is not None:
            self.set_policy_store(store)
        self._policy_version_provider = lambda: str(getattr(policy, "version", getattr(self._policy_store, "version", "")))

    @property
    def policy_version(self) -> str:
        if self._policy_version_provider is not None:
            return self._policy_version_provider()
        return self._policy_store.version

    def rules(self) -> list[Any]:
        if self._rules_provider is not None:
            rules = list(self._rules_provider())
        else:
            rules = self._policy_store.rules()
        return rules or _fallback_rules()

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        match = match_rules(self.rules(), event, trajectory_window)
        metadata = {
            "rule_based_plugin": match.to_dict(),
            "policy_version": self.policy_version,
        }
        if not match.matched or match.rule is None or match.effect is None:
            return CheckResult(metadata=metadata)

        decision = _decision_from_match(
            event=event,
            match=match,
            policy_version=self.policy_version,
        )
        return CheckResult(
            decision_candidate=decision,
            risk_signals=[],
            is_final=True,
            metadata=metadata,
        )


def _decision_from_match(
    *,
    event: RuntimeEvent,
    match: RuleMatch,
    policy_version: str,
) -> GuardDecision:
    dtype = effect_to_decision(match.effect)
    explanation = (
        f"rule '{match.rule.rule_id}' ({match.effect}) won among "
        f"{[r.rule_id for r in match.all_matched or []]}"
    )
    return GuardDecision(
        decision_type=dtype,
        reason=match.reason or explanation,
        policy_id=f"server:{match.rule.rule_id}",
        risk_signals=list(event.risk_signals),
        metadata={
            "explanation": explanation,
            "matched_rule_ids": [r.rule_id for r in match.all_matched or []],
            "policy_version": policy_version,
        },
    )


def _fallback_rules() -> list[PolicyRule]:
    return [
        PolicyRule(
            rule_id="deny_secret_exfiltration",
            effect=PolicyEffect.DENY,
            reason="Secret-like content combined with external send.",
            priority=100,
            event_types=["tool_invoke"],
            capabilities=[CAP_EXTERNAL_SEND],
            risk_signals=["secret_detected", "api_key_detected", "system_prompt_leak"],
        ),
        PolicyRule(
            rule_id="review_external_send",
            effect=PolicyEffect.REQUIRE_REMOTE_REVIEW,
            reason="External send is high-risk and needs remote review.",
            priority=60,
            event_types=["tool_invoke"],
            capabilities=[CAP_EXTERNAL_SEND],
        ),
    ]
