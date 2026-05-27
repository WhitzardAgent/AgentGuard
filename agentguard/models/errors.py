"""AgentGuard exceptions."""

from __future__ import annotations

from typing import Any


class AgentGuardError(Exception):
    """Base for all AgentGuard exceptions."""


class DecisionDenied(AgentGuardError):
    """Raised when the enforcer blocks a tool call."""

    def __init__(self, reason: str, matched_rules: list[str] | None = None,
                 request_id: str | None = None, **extra: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.matched_rules = matched_rules or []
        self.request_id = request_id
        self.extra = extra

    def to_structured(self) -> dict[str, Any]:
        return {
            "agentguard_denied": True,
            "reason": self.reason,
            "matched_rules": self.matched_rules,
            "suggestion": self.extra.get("suggestion", ""),
            "request_id": self.request_id,
        }


class HumanApprovalPending(AgentGuardError):
    """Raised when a call needs human approval and the caller is in suspend mode."""

    def __init__(self, ticket_id: str, reason: str = "human_approval_required") -> None:
        super().__init__(reason)
        self.ticket_id = ticket_id
        self.reason = reason

    def to_structured(self) -> dict[str, Any]:
        return {
            "agentguard_pending": True,
            "reason": self.reason,
            "ticket_id": self.ticket_id,
        }


class RuleCompileError(AgentGuardError):
    """Raised when a DSL rule fails to parse / compile."""
