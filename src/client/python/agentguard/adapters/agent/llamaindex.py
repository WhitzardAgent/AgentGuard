"""LlamaIndex agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class LlamaIndexAgentAdapter(BaseAgentAdapter):
    name = "llamaindex"

    def can_wrap(self, agent: Any) -> bool:
        mod = type(agent).__module__ or ""
        return "llama_index" in mod or "llamaindex" in mod

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("chat", "query", "run"):
            fn = getattr(agent, method, None)
            if callable(fn):
                try:
                    return str(fn(prompt))
                except Exception as exc:
                    raise AdapterError(f"llamaindex agent call failed: {exc}") from exc
        raise AdapterError("llamaindex agent exposes no chat/query/run")
