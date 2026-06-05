"""Anthropic (Claude) adapter."""

from __future__ import annotations

import logging
import os
from typing import Any

from agentguard.adapters.base import BaseAdapter

log = logging.getLogger("agentguard.adapters")


class AnthropicAdapter(BaseAdapter):
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-latest",
        *,
        client: Any = None,
        api_key: str | None = None,
        max_tokens: int = 1024,
        **options: Any,
    ) -> None:
        super().__init__(model=model, **options)
        self._client = client
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        try:
            import anthropic  # type: ignore
        except ImportError:
            return None
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _complete(self, prompt: str) -> str:
        client = self._ensure_client()
        if client is None:
            return super()._complete(prompt)
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(getattr(b, "text", "") for b in resp.content)
        except Exception as exc:  # noqa: BLE001
            log.warning("anthropic completion failed (%s); using offline fallback", exc)
            return super()._complete(prompt)
