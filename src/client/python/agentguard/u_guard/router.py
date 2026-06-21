"""U-Guard router: decide local vs remote vs cache vs fallback."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentguard.plugins.base import CheckResult
from agentguard.schemas.decisions import DecisionType
from agentguard.schemas.events import RuntimeEvent
from agentguard.tools.capability import HIGH_RISK_CAPABILITIES
from agentguard.u_guard.local_engine import LocalEvaluation

_UNCERTAIN_SIGNALS = {
    "prompt_injection",
    "tool_result_injection",
    "secret_detected",
    "api_key_detected",
}


class RouteTarget(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CACHE = "cache"
    FALLBACK = "fallback"


@dataclass
class RouteDecision:
    target: RouteTarget
    reason: str


class UGuardRouter:
    """Pure routing logic; makes no network calls itself."""

    def __init__(self, escalate_high_risk: bool = True) -> None:
        self.escalate_high_risk = escalate_high_risk

    def route(
        self,
        event: RuntimeEvent,
        local_eval: LocalEvaluation,
        check: CheckResult,
        *,
        server_available: bool,
        extension_requests_remote: bool = False,
        force_remote: bool = False,
    ) -> RouteDecision:
        decision = local_eval.decision
        dtype = decision.decision_type

        # 1. A final local plugin verdict wins immediately.
        if check.is_final and check.decision_candidate is not None:
            return RouteDecision(RouteTarget.LOCAL, "final local plugin verdict")

        # 2. Explicit local deny is authoritative.
        if dtype == DecisionType.DENY and local_eval.certain:
            return RouteDecision(RouteTarget.LOCAL, "clear local violation")

        # 3. Determine whether remote review is warranted.
        caps = set(getattr(event.payload, "capabilities", []) or [])
        high_risk = self.escalate_high_risk and bool(caps & HIGH_RISK_CAPABILITIES)
        wants_remote = (
            force_remote
            or extension_requests_remote
            or dtype == DecisionType.REQUIRE_REMOTE_REVIEW
            or high_risk
            or not local_eval.certain
            or bool(set(event.risk_signals) & _UNCERTAIN_SIGNALS)
        )

        if wants_remote:
            if server_available:
                return RouteDecision(RouteTarget.REMOTE, "high-risk or uncertain -> remote")
            return RouteDecision(RouteTarget.FALLBACK, "remote unavailable -> fallback")

        # 4. Certain, low-risk local decision.
        return RouteDecision(RouteTarget.LOCAL, "low-risk certain local decision")
