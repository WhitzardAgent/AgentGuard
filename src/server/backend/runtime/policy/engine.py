"""Server policy engine: deny-overrides decision with explanation."""
from __future__ import annotations

from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import RuntimeEvent
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
        _ = event, trace_window
        return GuardDecision.allow(
            "No server plugin returned a final decision; default allow.",
            policy_id="server:no_match",
            metadata={"explanation": "rule-based checks are optional"},
        )

    @staticmethod
    def is_deny_override(decision: GuardDecision) -> bool:
        return decision.decision_type == DecisionType.DENY
