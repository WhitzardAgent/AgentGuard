"""Server policy engine: deny-overrides decision with explanation."""
from __future__ import annotations

from agentguard.rules.matcher import match_rules
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.policy import effect_to_decision
from backend.runtime.policy.store import PolicyStore


class PolicyEngine:
    """Authoritative server-side policy decision (deny-overrides)."""

    def __init__(self, store: PolicyStore | None = None) -> None:
        self.store = store or PolicyStore.default()

    @property
    def version(self) -> str:
        return self.store.version

    def decide(
        self, event: RuntimeEvent, trace_window: list[RuntimeEvent] | None = None
    ) -> GuardDecision:
        match = match_rules(self.store.rules(), event, trace_window)
        if not match.matched or match.rule is None:
            return GuardDecision.allow(
                "No server rule matched; default allow.",
                policy_id="server:no_match",
                metadata={"explanation": "no matching rule"},
            )
        dtype = effect_to_decision(match.effect)
        explanation = (
            f"rule '{match.rule.rule_id}' ({match.effect.value}) won among "
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
                "policy_version": self.version,
            },
        )

    @staticmethod
    def is_deny_override(decision: GuardDecision) -> bool:
        return decision.decision_type == DecisionType.DENY
