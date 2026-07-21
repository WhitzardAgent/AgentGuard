"""Base class for deterministic audit rules."""
from __future__ import annotations

from abc import ABC, abstractmethod

from backend.audit.agent_models import AgentAuditContext, AuditFinding, SessionTrace


class BaseAuditRule(ABC):
    rule_id = "AUDIT-BASE"
    name = "Base audit rule"
    description = ""

    @abstractmethod
    def evaluate(
        self,
        context: AgentAuditContext,
        sessions: list[SessionTrace],
    ) -> list[AuditFinding]:
        """Return evidence-backed findings for the supplied snapshot."""


__all__ = ["BaseAuditRule"]
