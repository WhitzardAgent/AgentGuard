"""Fallback guard used when the remote server is unavailable."""
from __future__ import annotations

from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.tools.capability import HIGH_RISK_CAPABILITIES

_STRONG_SIGNALS = {
    "secret_detected",
    "api_key_detected",
    "system_prompt_leak",
    "prompt_injection",
    "tool_result_injection",
    "unsafe_final_response",
}


class FallbackGuard:
    """Conservative local decision when remote review cannot complete."""

    def __init__(self, fail_closed: bool = True) -> None:
        self.fail_closed = fail_closed

    def decide(self, event: RuntimeEvent) -> GuardDecision:
        caps = set(event.payload.get("capabilities") or [])
        signals = set(event.risk_signals)
        high_risk = bool(caps & HIGH_RISK_CAPABILITIES) or bool(signals & _STRONG_SIGNALS)
        if high_risk and self.fail_closed:
            return GuardDecision.require_approval(
                "Remote review unavailable; high-risk action held for approval.",
                policy_id="fallback:fail_closed",
                risk_signals=sorted(signals),
                metadata={"fallback": True},
            )
        return GuardDecision.log_only(
            "Remote review unavailable; low-risk action allowed with logging.",
            policy_id="fallback:fail_open",
            risk_signals=sorted(signals),
            metadata={"fallback": True},
        )
