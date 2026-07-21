"""Base interfaces for agent-wide and LLM-assisted auditors."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from backend.audit.agent_models import AgentAuditContext, AgentAuditResult, SessionTrace


class BaseAgentAuditor(ABC):
    name = "base_agent_auditor"
    description = ""

    @abstractmethod
    def audit(
        self,
        context: AgentAuditContext,
        sessions: Iterable[SessionTrace],
    ) -> AgentAuditResult:
        """Audit all sessions in a stable agent snapshot."""


class BaseLLMAgentAuditor(BaseAgentAuditor):
    prompt_version = "agent-security-v2"

    @abstractmethod
    def model_name(self) -> str | None:
        """Return the configured model name without exposing credentials."""


__all__ = ["BaseAgentAuditor", "BaseLLMAgentAuditor"]
