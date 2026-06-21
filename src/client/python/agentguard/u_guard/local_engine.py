"""Local guard engine: evaluate a policy snapshot into a GuardDecision."""
from __future__ import annotations

from dataclasses import dataclass

from agentguard.rules.matcher import MatchResult
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.policy import effect_to_decision
from agentguard.u_guard.policy_snapshot import PolicySnapshot


@dataclass
class LocalEvaluation:
    decision: GuardDecision
    match: MatchResult
    certain: bool


class LocalGuardEngine:
    """Wraps a policy snapshot and produces a local decision + certainty."""

    def __init__(self, snapshot: PolicySnapshot | None = None) -> None:
        self.snapshot = snapshot or PolicySnapshot.default()

    def set_snapshot(self, snapshot: PolicySnapshot) -> None:
        self.snapshot = snapshot

    def evaluate(
        self, event: RuntimeEvent, trace_window: list[RuntimeEvent] | None = None
    ) -> LocalEvaluation:
        match = self.snapshot.evaluate(event, trace_window)
        if not match.matched or match.rule is None:
            decision = GuardDecision.allow(
                "No matching rule; default allow.", policy_id="local:no_match"
            )
            certain = not event.risk_signals
            return LocalEvaluation(decision=decision, match=match, certain=certain)

        dtype = effect_to_decision(match.effect)
        decision = GuardDecision(
            decision_type=dtype,
            reason=match.reason,
            policy_id=f"local:{match.rule.rule_id}",
            risk_signals=list(event.risk_signals),
            metadata={"matched_rule_ids": [r.rule_id for r in match.all_matched or []]},
        )
        # A non-default explicit rule is a certain local decision. A default
        # allow is certain only when there are no outstanding risk signals.
        is_default = match.rule.priority == 0 and dtype == DecisionType.ALLOW
        certain = (not is_default) or (not event.risk_signals)
        return LocalEvaluation(decision=decision, match=match, certain=certain)
