"""OpenAI chat completion adapter."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.llm.base import BaseLLMAdapter


class OpenAILLMAdapter(BaseLLMAdapter):
    name = "openai"

    def can_wrap(self, llm: Any) -> bool:
        mod = type(llm).__module__ or ""
        return "openai" in mod

    def normalize_response(self, response: Any) -> Any:
        try:
            choice = response.choices[0].message
            return {
                "text": getattr(choice, "content", None),
                "tool_calls": [
                    {
                        "id": tc.id,
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in (getattr(choice, "tool_calls", None) or [])
                ],
            }
        except (AttributeError, IndexError, TypeError):
            return response

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        create = getattr(getattr(getattr(llm, "chat", None), "completions", None), "create", None)
        if callable(create):
            return create(**request) if isinstance(request, dict) else create(messages=request, **kwargs)
        return super().complete(llm, request, **kwargs)
