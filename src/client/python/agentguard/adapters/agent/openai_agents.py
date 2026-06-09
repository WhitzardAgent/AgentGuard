"""OpenAI Agents SDK adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class OpenAIAgentsAdapter(BaseAgentAdapter):
    name = "openai_agents"

    def can_wrap(self, agent: Any) -> bool:
        mod = type(agent).__module__ or ""
        return "agents" in mod and "openai" in mod

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        fn = getattr(agent, "run", None) or getattr(agent, "invoke", None)
        if callable(fn):
            try:
                return fn(prompt)
            except Exception as exc:
                raise AdapterError(f"openai agents run failed: {exc}") from exc
        raise AdapterError("openai agent exposes no run/invoke")
