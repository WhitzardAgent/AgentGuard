"""Base interfaces for agent-wide and LLM-assisted auditors."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, Self

from backend.audit.agent_models import AgentAuditContext, AgentAuditResult, SessionTrace


class BaseAgentAuditor(ABC):
    name = "base_agent_auditor"
    description = ""

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> Self:
        """Build an auditor for one run, optionally using request-scoped configuration."""
        return cls()

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
