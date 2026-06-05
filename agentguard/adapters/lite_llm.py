"""LiteLLM adapter — routes completions through the ``litellm`` proxy SDK."""

from __future__ import annotations

import logging
from typing import Any

from agentguard.adapters.base import BaseAdapter

log = logging.getLogger("agentguard.adapters")


class LiteLLMAdapter(BaseAdapter):
    provider = "litellm"

    def __init__(self, model: str = "gpt-3.5-turbo", *, temperature: float = 0.2, **options: Any) -> None:
        super().__init__(model=model, **options)
        self.temperature = temperature

    def _complete(self, prompt: str) -> str:
        try:
            import litellm  # type: ignore
        except ImportError:
            return super()._complete(prompt)
        try:
            resp = litellm.completion(
                model=self.model,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("litellm completion failed (%s); using offline fallback", exc)
            return super()._complete(prompt)
