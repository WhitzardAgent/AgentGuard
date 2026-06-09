"""Google Gemini adapter."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.llm.base import BaseLLMAdapter


class GeminiLLMAdapter(BaseLLMAdapter):
    name = "gemini"

    def can_wrap(self, llm: Any) -> bool:
        mod = type(llm).__module__ or ""
        return "google" in mod and "generativeai" in mod or "genai" in mod

    def normalize_response(self, response: Any) -> Any:
        text = getattr(response, "text", None)
        return {"text": text} if text is not None else response

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        fn = getattr(llm, "generate_content", None)
        if callable(fn):
            return fn(request, **kwargs)
        return super().complete(llm, request, **kwargs)
