"""Decision schema shared by policy engine and enforcement layer.

Decision flow
─────────────
┌────────────────────────────────────────────────────────────┐
│  Server-side Action (4 values)         →  ClientAction (3)  │
│                                                              │
│  ALLOW    ─────────────────────────────→  ALLOW             │
│  DENY     ─────────────────────────────→  DENY              │
│  LLM_CHECK  (LLM reviews internally)  →  ALLOW / DENY /     │
│                                            HUMAN_CHECK       │
│  DEGRADE  (params rewritten, execute) →  ALLOW              │
└────────────────────────────────────────────────────────────┘

``ClientAction`` is what SDK clients and the HTTP API surface receive.
``Action`` (4 values) is the server's internal policy language.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────────
# Server-side action enum  (used in DSL rules and internal pipeline)
# ──────────────────────────────────────────────────────────────────────────────

class Action(str, Enum):
    """Internal server-side decision actions — 4 values.

    Rules authors use these in THEN clauses.
    """
    ALLOW = "allow"
    DENY = "deny"
    LLM_CHECK = "llm_check"   # Server invokes LLM reviewer; resolved before response
    DEGRADE = "degrade"       # Server rewrites parameters, then executes

    # Backward-compat alias kept so existing builtin rules that write
    # ``THEN HUMAN_CHECK`` continue to compile and behave as direct-escalation
    # (no LLM intermediary — immediately queued for human review).
    HUMAN_CHECK = "human_check"

    @property
    def priority(self) -> int:
        """Lower number = higher precedence when merging decisions."""
        return {
            Action.DENY: 0,
            Action.LLM_CHECK: 1,    # uncertain — resolve before final answer
            Action.HUMAN_CHECK: 2,  # direct escalation (legacy / explicit)
            Action.DEGRADE: 3,
            Action.ALLOW: 4,
        }[self]


# ──────────────────────────────────────────────────────────────────────────────
# Client-facing action enum  (3 values — returned to SDK / HTTP callers)
# ──────────────────────────────────────────────────────────────────────────────

class ClientAction(str, Enum):
    """External decision vocabulary returned to agent SDKs and HTTP clients.

    Clients MUST honour all three:
    * ALLOW       — proceed with the (possibly degraded) tool call.
    * DENY        — abort; do not invoke the tool.
    * HUMAN_CHECK — pause and wait for human approval before retrying.
    """
    ALLOW = "allow"
    DENY = "deny"
    HUMAN_CHECK = "human_check"


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

class Obligation(BaseModel):
    """Side-effect that enforcer MUST apply in order."""

    kind: str  # "mask_field" | "rewrite_tool" | "rate_limit" | ...
    params: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    action: Action
    risk_score: float = 0.0
    matched_rules: list[str] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    rule_version: str = "unknown"
    ttl_ms: int = 0
    reason: str = ""
    degrade_profile: str | None = None
    llm_system_prompt: str | None = Field(default=None, exclude=True)

    # ── client-visible fields (populated by Enforcer / API layer) ────────────
    client_action: ClientAction | None = None
    """Resolved client-facing action (set after LLM_CHECK / DEGRADE resolution).
    None until the enforcer has resolved the server action."""

    @classmethod
    def allow(cls, *, reason: str = "no-rule-matched", rule_version: str = "unknown") -> "Decision":
        return cls(action=Action.ALLOW, reason=reason, rule_version=rule_version)

    def to_client_action(self) -> ClientAction:
        """Map the server-side action to the 3-value client vocabulary.

        ALLOW      → ClientAction.ALLOW
        DENY       → ClientAction.DENY
        HUMAN_CHECK → ClientAction.HUMAN_CHECK   (direct escalation)
        LLM_CHECK  → ClientAction.HUMAN_CHECK    (LLM unresolved → escalate)
        DEGRADE    → ClientAction.ALLOW          (params rewritten, proceed)
        """
        if self.client_action is not None:
            return self.client_action
        _MAP: dict[Action, ClientAction] = {
            Action.ALLOW:       ClientAction.ALLOW,
            Action.DENY:        ClientAction.DENY,
            Action.HUMAN_CHECK: ClientAction.HUMAN_CHECK,
            Action.LLM_CHECK:   ClientAction.HUMAN_CHECK,   # fallback if unresolved
            Action.DEGRADE:     ClientAction.ALLOW,
        }
        return _MAP[self.action]
