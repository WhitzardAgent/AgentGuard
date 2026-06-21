"""Custom LLM adapter: llm is any callable."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.llm.base import BaseLLMAdapter


class CustomLLMAdapter(BaseLLMAdapter):
    name = "custom"

    def can_wrap(self, llm: Any) -> bool:
        return callable(llm) or hasattr(llm, "complete") or hasattr(llm, "generate")

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        if callable(llm):
            return llm(request, **kwargs)
        for method in ("complete", "generate"):
            fn = getattr(llm, method, None)
            if callable(fn):
                return fn(request, **kwargs)
        raise ValueError("custom llm not callable")
