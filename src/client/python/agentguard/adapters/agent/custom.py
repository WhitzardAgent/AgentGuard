"""Custom agent adapter: agent is a callable or has a generate()/step() method."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class CustomAgentAdapter(BaseAgentAdapter):
    name = "custom"

    def can_wrap(self, agent: Any) -> bool:
        return (
            callable(agent)
            or hasattr(agent, "generate")
            or hasattr(agent, "step")
        )

    def patchtool(self, agent: Any, guard: Any) -> int:
        return 0

    def patchLLM(self, agent: Any, guard: Any) -> int:
        return 0

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        if hasattr(agent, "generate"):
            return agent.generate(messages)
        if hasattr(agent, "step"):
            return agent.step(messages)
        if callable(agent):
            return agent(messages)
        raise AdapterError("custom agent is not callable")
