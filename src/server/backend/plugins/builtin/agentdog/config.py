"""AgentDoG server plugin configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AgentDoGServerConfig:
    # "model" uses a served AgentDoG checkpoint; "heuristic" is the offline analyzer.
    backend: str = "heuristic"
    api_base: str | None = None
    model: str = "agentdog"
    api_key: str = ""
    timeout_s: float = 30.0
    min_score_to_flag: float = 0.5

    @classmethod
    def from_env(cls) -> "AgentDoGServerConfig":
        """Prefer the real model judge when an endpoint is configured."""
        api_base = os.environ.get("AGENTDOG_API_BASE") or os.environ.get("AGENTDOG_BASE_URL")
        if api_base:
            return cls(
                backend="model",
                api_base=api_base,
                model=os.environ.get("AGENTDOG_MODEL", "agentdog"),
                api_key=os.environ.get("AGENTDOG_API_KEY", ""),
                timeout_s=float(os.environ.get("AGENTDOG_TIMEOUT_S", "30")),
                min_score_to_flag=float(os.environ.get("AGENTDOG_MIN_SCORE", "0.5")),
            )
        return cls(backend="heuristic")
