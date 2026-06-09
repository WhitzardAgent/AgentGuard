"""LangChain agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


def _module_name(obj: Any) -> str:
    return type(obj).__module__ or ""


class LangChainAgentAdapter(BaseAgentAdapter):
    name = "langchain"

    def can_wrap(self, agent: Any) -> bool:
        return "langchain" in _module_name(agent)

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("invoke", "run", "predict"):
            fn = getattr(agent, method, None)
            if callable(fn):
                try:
                    return fn(prompt)
                except Exception as exc:
                    raise AdapterError(f"langchain agent invoke failed: {exc}") from exc
        raise AdapterError("langchain agent exposes no invoke/run/predict")
