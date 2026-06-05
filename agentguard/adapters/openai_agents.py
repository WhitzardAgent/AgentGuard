"""OpenAI adapter with LLM thought interception.

Uses the ``openai`` SDK when installed and an API key is configured; otherwise
falls back to the deterministic offline reasoning loop so demos and tests run
without network access.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agentguard.adapters.base import BaseAdapter

log = logging.getLogger("agentguard.adapters")


class OpenAIAdapter(BaseAdapter):
    provider = "openai"

    def __init__(
        self,
        model: str = "gpt-4",
        *,
        client: Any = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        **options: Any,
    ) -> None:
        super().__init__(model=model, **options)
        self._client = client
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.temperature = temperature

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        try:
            import openai  # type: ignore
        except ImportError:
            return None
        self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def _complete(self, prompt: str) -> str:
        client = self._ensure_client()
        if client is None:
            return super()._complete(prompt)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("openai completion failed (%s); using offline fallback", exc)
            return super()._complete(prompt)
