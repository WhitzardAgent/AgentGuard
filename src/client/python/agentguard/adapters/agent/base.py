"""Agent adapter interface and guarded-agent wrapper."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class GuardedAgent:
    """A guarded agent bound to a runtime and an adapter."""

    def __init__(self, agent: Any, adapter: "BaseAgentAdapter", runtime: Any) -> None:
        self._agent = agent
        self._adapter = adapter
        self._runtime = runtime

    def run(self, input_data: Any) -> dict[str, Any]:
        return self._runtime.run_agent(self._adapter, self._agent, input_data)

    def __call__(self, input_data: Any) -> dict[str, Any]:
        return self.run(input_data)


class BaseAgentAdapter:
    name: str = "base"

    def can_wrap(self, agent: Any) -> bool:
        raise NotImplementedError

    def wrap(self, agent: Any, runtime: Any) -> GuardedAgent:
        return GuardedAgent(agent, self, runtime)

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


def select_agent_adapter(agent: Any, adapters: list[BaseAgentAdapter]) -> BaseAgentAdapter:
    for adapter in adapters:
        try:
            if adapter.can_wrap(agent):
                return adapter
        except Exception:
            continue
    raise AdapterError("no agent adapter can wrap the given agent")
