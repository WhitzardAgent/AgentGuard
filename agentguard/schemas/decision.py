"""Decision vocabulary enforced by the client-side PEP.

The Harness/PEP supports the full enforcement vocabulary required by the
target design:

* ``allow``            — proceed unchanged
* ``deny``             — abort the behaviour
* ``degrade``          — execute a downgraded / reduced-capability variant
* ``ask_user``         — pause and ask the human in the loop
* ``sanitize``         — execute but with content/args scrubbed first
* ``log_only``         — record but otherwise allow (typically for thoughts)
* ``require_approval`` — block until an out-of-band approval is granted
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DecisionAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    DEGRADE = "degrade"
    ASK_USER = "ask_user"
    SANITIZE = "sanitize"
    LOG_ONLY = "log_only"
    REQUIRE_APPROVAL = "require_approval"

    @property
    def blocks_execution(self) -> bool:
        return self in {DecisionAction.DENY, DecisionAction.REQUIRE_APPROVAL}

    @property
    def precedence(self) -> int:
        """Lower = wins when merging multiple matched decisions."""
        return {
            DecisionAction.DENY: 0,
            DecisionAction.REQUIRE_APPROVAL: 1,
            DecisionAction.ASK_USER: 2,
            DecisionAction.SANITIZE: 3,
            DecisionAction.DEGRADE: 4,
            DecisionAction.LOG_ONLY: 5,
            DecisionAction.ALLOW: 6,
        }[self]


class Obligation(BaseModel):
    """A side-effect the enforcer MUST apply when honouring a decision.

    Examples: ``mask_field`` redact an argument, ``truncate`` shorten content,
    ``redirect_tool`` swap to a safer tool.
    """

    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    action: DecisionAction = DecisionAction.ALLOW
    reason: str = ""
    risk_score: float = 0.0
    matched_rules: list[str] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    source: str = "local"  # "local" | "pdp" | "fallback" | "cache"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def allow(cls, *, reason: str = "no_rule_matched", source: str = "local") -> "Decision":
        return cls(action=DecisionAction.ALLOW, reason=reason, source=source)

    @classmethod
    def deny(cls, *, reason: str, matched_rules: list[str] | None = None) -> "Decision":
        return cls(
            action=DecisionAction.DENY,
            reason=reason,
            matched_rules=matched_rules or [],
            risk_score=1.0,
        )

    def merge(self, other: "Decision") -> "Decision":
        """Return whichever decision has higher precedence, keeping both rule ids."""
        winner = self if self.action.precedence <= other.action.precedence else other
        merged_rules = list(dict.fromkeys([*self.matched_rules, *other.matched_rules]))
        return winner.model_copy(
            update={
                "matched_rules": merged_rules,
                "risk_score": max(self.risk_score, other.risk_score),
                "obligations": [*self.obligations, *other.obligations],
            }
        )
