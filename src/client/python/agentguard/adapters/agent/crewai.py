"""CrewAI agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class CrewAIAgentAdapter(BaseAgentAdapter):
    name = "crewai"

    def can_wrap(self, agent: Any) -> bool:
        return "crewai" in (type(agent).__module__ or "")

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("kickoff", "execute_task", "run"):
            fn = getattr(agent, method, None)
            if callable(fn):
                try:
                    return str(fn(prompt))
                except Exception as exc:
                    raise AdapterError(f"crewai agent call failed: {exc}") from exc
        raise AdapterError("crewai agent exposes no kickoff/execute_task/run")
