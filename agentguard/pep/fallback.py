"""Fallback behaviour when the PDP is unreachable."""

from __future__ import annotations

from agentguard.schemas.decision import Decision, DecisionAction


class FallbackPolicy:
    """Resolves a decision when neither PDP nor local rules are authoritative.

    ``fail_open=True``  → allow (availability over strictness)
    ``fail_open=False`` → require approval (strictness over availability)
    """

    def __init__(self, *, fail_open: bool = True) -> None:
        self.fail_open = fail_open

    def on_pdp_unavailable(self, local: Decision | None) -> Decision:
        if local is not None:
            return local.model_copy(update={"source": "fallback"})
        if self.fail_open:
            return Decision(
                action=DecisionAction.ALLOW,
                reason="pdp_unavailable_fail_open",
                source="fallback",
            )
        return Decision(
            action=DecisionAction.REQUIRE_APPROVAL,
            reason="pdp_unavailable_fail_closed",
            source="fallback",
            risk_score=0.5,
        )
