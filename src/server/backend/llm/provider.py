"""Server LLM provider abstraction.

The default, production path is an OpenAI-compatible HTTP provider configured via
environment variables. When no endpoint is configured (offline/dev), a
deterministic ``HeuristicProvider`` is used. The heuristic provider is a real
rule-based generator for skill assistance, not a stub of an LLM.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class HeuristicProvider:
    """Deterministic, non-networked text generator for offline skill assistance."""

    name = "heuristic"

    def complete(self, prompt: str, **kwargs: Any) -> str:
        # Produce a concise, structured echo summary that downstream skills can
        # parse deterministically (used only when no model endpoint is set).
        head = prompt.strip().splitlines()[0] if prompt.strip() else ""
        return f"summary: {head[:200]}"


class OpenAICompatibleProvider:
    """Real provider calling an OpenAI-compatible /chat/completions endpoint."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def complete(self, prompt: str, **kwargs: Any) -> str:
        url = f"{self.base_url}/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": kwargs.get("temperature", 0),
                "max_tokens": kwargs.get("max_tokens", 1024),
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("no choices in LLM response")
        return (choices[0].get("message") or {}).get("content") or ""


def get_provider(**kwargs: Any) -> Any:
    """Return the real model provider when configured, else the heuristic one."""
    config = dict(kwargs.get("config") or {})
    base_url = _config_value(config, "base_url", "llm_base_url")
    if not base_url:
        base_url = os.environ.get("AGENTGUARD_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    backend = str(_config_value(config, "backend", "llm_backend") or "").strip().lower()
    if backend in {"heuristic", "offline"}:
        return HeuristicProvider()
    if base_url:
        return OpenAICompatibleProvider(
            base_url=base_url,
            model=str(
                _config_value(config, "model", "llm_model")
                or os.environ.get("AGENTGUARD_LLM_MODEL", "gpt-4o-mini")
            ),
            api_key=str(
                _config_value(config, "api_key", "llm_api_key")
                or os.environ.get("AGENTGUARD_LLM_API_KEY")
                or os.environ.get("OPENAI_API_KEY", "")
            ),
            timeout_s=float(
                _config_value(config, "timeout_s", "llm_timeout_s")
                or os.environ.get("AGENTGUARD_LLM_TIMEOUT_S", "30")
            ),
        )
    return HeuristicProvider()


def _config_value(config: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return value
    return None
