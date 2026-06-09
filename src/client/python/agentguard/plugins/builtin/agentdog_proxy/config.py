"""AgentDoG client proxy configuration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentDoGProxyConfig:
    enabled: bool = True
    window_size: int = 8
    redaction_level: str = "standard"
    include_tool_results: bool = True
    include_llm_outputs: bool = True
    force_remote_on_high_risk: bool = True
