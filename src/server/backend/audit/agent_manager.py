"""Factory and manager for agent-wide auditors."""
from __future__ import annotations

from typing import Any

from backend.audit.agent_base import BaseAgentAuditor
from backend.audit.auditors.hybrid_agent_security import HybridAgentSecurityAuditor
from backend.audit.auditors.llm_agent_security import LLMAgentSecurityAuditor
from backend.audit.auditors.rule_agent_security import RuleBasedAgentSecurityAuditor
from backend.audit.llm_client import AuditLLMClient


class AgentAuditorManager:
    def __init__(self) -> None:
        self._auditors: dict[str, BaseAgentAuditor] = {}

    def get(
        self,
        name: str,
        *,
        llm_config: dict[str, Any] | None = None,
    ) -> BaseAgentAuditor:
        normalized = str(name or "hybrid_agent_security").strip()
        if not llm_config and normalized in self._auditors:
            return self._auditors[normalized]
        if normalized == "rule_agent_security":
            auditor: BaseAgentAuditor = RuleBasedAgentSecurityAuditor()
        elif normalized == "llm_agent_security":
            auditor = LLMAgentSecurityAuditor(config=llm_config)
        elif normalized == "hybrid_agent_security":
            auditor = HybridAgentSecurityAuditor(
                llm_auditor=LLMAgentSecurityAuditor(
                    client=AuditLLMClient(config=llm_config),
                    config=llm_config,
                )
            )
        else:
            raise ValueError(f"unknown agent auditor: {normalized}")
        if not llm_config:
            self._auditors[normalized] = auditor
        return auditor

    @staticmethod
    def descriptions() -> list[dict[str, str]]:
        return [
            {"name": "hybrid_agent_security", "description": HybridAgentSecurityAuditor.description},
            {"name": "rule_agent_security", "description": RuleBasedAgentSecurityAuditor.description},
            {"name": "llm_agent_security", "description": LLMAgentSecurityAuditor.description},
        ]


__all__ = ["AgentAuditorManager"]
