"""Thin server LLM client wrapper."""
from __future__ import annotations

from typing import Any

from backend.llm.provider import get_provider


class LLMClient:
    def __init__(self, provider: Any = None, *, config: dict[str, Any] | None = None) -> None:
        self.provider = provider or get_provider(config=config or {})

    def complete(self, prompt: str, **kwargs: Any) -> str:
        return self.provider.complete(prompt, **kwargs)
