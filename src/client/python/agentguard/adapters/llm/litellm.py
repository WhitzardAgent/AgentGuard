"""LiteLLM normalized adapter."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.llm.base import BaseLLMAdapter


class LiteLLMAdapter(BaseLLMAdapter):
    name = "litellm"

    def can_wrap(self, llm: Any) -> bool:
        mod = getattr(llm, "__module__", "") or type(llm).__module__ or ""
        return "litellm" in mod

    def normalize_response(self, response: Any) -> Any:
        try:
            msg = response["choices"][0]["message"]
            return {"text": msg.get("content"), "tool_calls": msg.get("tool_calls") or []}
        except (KeyError, IndexError, TypeError):
            return response

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        # litellm.completion is a module-level callable.
        fn = llm if callable(llm) else getattr(llm, "completion", None)
        if callable(fn):
            return fn(**request) if isinstance(request, dict) else fn(messages=request, **kwargs)
        return super().complete(llm, request, **kwargs)
