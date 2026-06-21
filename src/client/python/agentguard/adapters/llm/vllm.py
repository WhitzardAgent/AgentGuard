"""vLLM adapter (OpenAI-compatible or native LLM engine)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.llm.base import BaseLLMAdapter


class VLLMAdapter(BaseLLMAdapter):
    name = "vllm"

    def can_wrap(self, llm: Any) -> bool:
        return "vllm" in (type(llm).__module__ or "")

    def normalize_response(self, response: Any) -> Any:
        # vllm.LLM.generate returns a list of RequestOutput.
        try:
            if isinstance(response, list) and response:
                return {"text": response[0].outputs[0].text}
        except (AttributeError, IndexError):
            pass
        return response

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        fn = getattr(llm, "generate", None)
        if callable(fn):
            prompt = request if isinstance(request, str) else str(request)
            return fn(prompt, **kwargs)
        return super().complete(llm, request, **kwargs)
