"""Agent adapter interface for attach-mode integrations."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class BaseAgentAdapter:
    name: str = "base"

    def can_wrap(self, agent: Any) -> bool:
        raise NotImplementedError

    def attach(
        self,
        agent: Any,
        guard: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch a framework object in-place while preserving its native loop."""
        raise AdapterError(f"{self.name}: attach is not implemented")

    def run(self, agent: Any, input_data: Any, context: RuntimeContext) -> Any:
        """Raw, unguarded run of the underlying agent (best effort)."""
        if callable(agent):
            return agent(input_data)
        raise AdapterError(f"{self.name}: agent is not runnable")

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        """Produce one LLM turn given the running message list."""
        raise NotImplementedError
