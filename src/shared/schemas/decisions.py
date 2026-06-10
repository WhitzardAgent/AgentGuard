"""GuardDecision: the single decision type used across the framework."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionType(str, Enum):
    ALLOW = "allow"
    DENY = "deny"

    SANITIZE = "sanitize"
    REWRITE = "rewrite"
    REPAIR = "repair"

    DEGRADE = "degrade"
    ASK_USER = "ask_user"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_REMOTE_REVIEW = "require_remote_review"

    LOOP_BACK_TO_LLM = "loop_back_to_llm"
    DROP_THOUGHT = "drop_thought"
    ALIGN_THOUGHT = "align_thought"

    LOG_ONLY = "log_only"


# Decision types that block execution of the original action.
_BLOCKING = {
    DecisionType.DENY,
    DecisionType.DEGRADE,
    DecisionType.ASK_USER,
    DecisionType.REQUIRE_APPROVAL,
    DecisionType.DROP_THOUGHT,
}
_REQUIRES_USER = {DecisionType.ASK_USER, DecisionType.REQUIRE_APPROVAL}
_REQUIRES_REMOTE = {DecisionType.REQUIRE_REMOTE_REVIEW}


@dataclass
class GuardDecision:
    decision_type: DecisionType
    reason: str
    policy_id: str | None = None
    confidence: float | None = None
    risk_signals: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- properties ----------------------------------------------------
    @property
    def is_allow(self) -> bool:
        return self.decision_type == DecisionType.ALLOW

    @property
    def is_blocking(self) -> bool:
        return self.decision_type in _BLOCKING

    @property
    def requires_remote(self) -> bool:
        return self.decision_type in _REQUIRES_REMOTE

    @property
    def requires_user(self) -> bool:
        return self.decision_type in _REQUIRES_USER

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_type": self.decision_type.value,
            "reason": self.reason,
            "policy_id": self.policy_id,
            "confidence": self.confidence,
            "risk_signals": list(self.risk_signals),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuardDecision":
        return cls(
            decision_type=DecisionType(data["decision_type"]),
            reason=data.get("reason", ""),
            policy_id=data.get("policy_id"),
            confidence=data.get("confidence"),
            risk_signals=list(data.get("risk_signals") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    # ---- static constructors -------------------------------------------
    @staticmethod
    def allow(reason: str = "allowed", **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.ALLOW, reason, **kw)

    @staticmethod
    def deny(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.DENY, reason, **kw)

    @staticmethod
    def sanitize(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.SANITIZE, reason, **kw)

    @staticmethod
    def rewrite(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.REWRITE, reason, **kw)

    @staticmethod
    def repair(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.REPAIR, reason, **kw)

    @staticmethod
    def degrade(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.DEGRADE, reason, **kw)

    @staticmethod
    def ask_user(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.ASK_USER, reason, **kw)

    @staticmethod
    def require_approval(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.REQUIRE_APPROVAL, reason, **kw)

    @staticmethod
    def require_remote_review(reason: str, **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.REQUIRE_REMOTE_REVIEW, reason, **kw)

    @staticmethod
    def log_only(reason: str = "log only", **kw: Any) -> "GuardDecision":
        return GuardDecision(DecisionType.LOG_ONLY, reason, **kw)
