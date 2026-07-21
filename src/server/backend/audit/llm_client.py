"""Strict LLM client used by security audits."""
from __future__ import annotations

import os
from typing import Any

from backend.llm.provider import HeuristicProvider, get_provider


class LLMAuditUnavailable(RuntimeError):
    """Raised when a real audit model is not configured or reachable."""


class AuditLLMClient:
    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        explicit = dict(config or {})
        audit_config = {
            "base_url": explicit.get("base_url") or os.getenv("AGENTGUARD_AUDIT_LLM_BASE_URL"),
            "model": explicit.get("model") or os.getenv("AGENTGUARD_AUDIT_LLM_MODEL"),
            "api_key": explicit.get("api_key") or os.getenv("AGENTGUARD_AUDIT_LLM_API_KEY"),
            "timeout_s": explicit.get("timeout_s") or os.getenv("AGENTGUARD_AUDIT_LLM_TIMEOUT_S"),
        }
        self.provider = get_provider(config={key: value for key, value in audit_config.items() if value})
        self.model = str(
            audit_config.get("model")
            or os.getenv("AGENTGUARD_LLM_MODEL")
            or getattr(self.provider, "model", "")
        ).strip() or None

    @property
    def available(self) -> bool:
        return not isinstance(self.provider, HeuristicProvider)

    def complete(self, prompt: str, *, max_tokens: int = 4096) -> str:
        if not self.available:
            raise LLMAuditUnavailable("A real LLM audit provider is not configured")
        try:
            result = self.provider.complete(prompt, temperature=0, max_tokens=max_tokens)
        except Exception as exc:
            raise LLMAuditUnavailable(f"LLM audit request failed: {type(exc).__name__}") from exc
        if not isinstance(result, str) or not result.strip():
            raise LLMAuditUnavailable("LLM audit returned an empty response")
        return result.strip()


__all__ = ["AuditLLMClient", "LLMAuditUnavailable"]
