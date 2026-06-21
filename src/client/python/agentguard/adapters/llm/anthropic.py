"""Anthropic messages adapter."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.llm.base import BaseLLMAdapter


class AnthropicLLMAdapter(BaseLLMAdapter):
    name = "anthropic"

    def can_wrap(self, llm: Any) -> bool:
        return "anthropic" in (type(llm).__module__ or "")

    def normalize_response(self, response: Any) -> Any:
        content = getattr(response, "content", None)
        if isinstance(content, list):
            text = " ".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text")
            tool_uses = [
                {"type": "tool_use", "name": getattr(b, "name", ""), "input": getattr(b, "input", {})}
                for b in content
                if getattr(b, "type", "") == "tool_use"
            ]
            return {"text": text, "tool_calls": tool_uses}
        return response

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        create = getattr(getattr(llm, "messages", None), "create", None)
        if callable(create):
            return create(**request) if isinstance(request, dict) else create(messages=request, **kwargs)
        return super().complete(llm, request, **kwargs)
