"""AgentDoG service: pick a backend adapter and run diagnosis."""
from __future__ import annotations

from typing import Any

from backend.plugins.builtin.agentdog.adapter import (
    AgentDoGModelAdapter,
    HeuristicAgentDoGAdapter,
)
from backend.plugins.builtin.agentdog.config import AgentDoGServerConfig
from backend.plugins.builtin.agentdog.schemas import AgentDoGDiagnosis


class AgentDoGService:
    def __init__(self, config: AgentDoGServerConfig | None = None) -> None:
        self.config = config or AgentDoGServerConfig.from_env()
        if self.config.backend == "model" and self.config.api_base:
            self.adapter = AgentDoGModelAdapter(
                self.config.api_base,
                model=self.config.model,
                api_key=self.config.api_key,
                timeout_s=self.config.timeout_s,
            )
        else:
            self.adapter = HeuristicAgentDoGAdapter()

    def diagnose(self, trajectory: list[dict[str, Any]]) -> AgentDoGDiagnosis:
        return self.adapter.diagnose(trajectory)
