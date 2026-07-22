"""Factory and manager for agent-wide auditors."""
from __future__ import annotations

from typing import Any

from backend.audit.agent_base import BaseAgentAuditor
from backend.audit.agent_registry import agent_auditor_descriptions, get_agent_auditor_class


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
        auditor_class = get_agent_auditor_class(normalized)
        if auditor_class is None:
            raise ValueError(f"unknown agent auditor: {normalized}")
        auditor = auditor_class.from_config(llm_config)
        if not llm_config:
            self._auditors[normalized] = auditor
        return auditor

    @staticmethod
    def descriptions() -> list[dict[str, str]]:
        return [
            {"name": name, "description": description}
            for name, description in sorted(agent_auditor_descriptions().items())
        ]


__all__ = ["AgentAuditorManager"]
