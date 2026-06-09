"""AgentDoG server plugin: diagnose trajectories and enrich decisions."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.decisions import GuardDecision
from backend.plugins.base import ServerPlugin
from backend.plugins.builtin.agentdog.config import AgentDoGServerConfig
from backend.plugins.builtin.agentdog.formatter import extract_trajectory
from backend.plugins.builtin.agentdog.mapper import map_diagnosis
from backend.plugins.builtin.agentdog.report import AgentDoGReportBuilder
from backend.plugins.builtin.agentdog.service import AgentDoGService


class AgentDoGServerPlugin(ServerPlugin):
    plugin_id = "agentdog"

    def __init__(self, config: AgentDoGServerConfig | None = None) -> None:
        self.config = config or AgentDoGServerConfig.from_env()
        self.service = AgentDoGService(self.config)
        self.report = AgentDoGReportBuilder()

    def on_diagnose(self, request: dict[str, Any], context: dict[str, Any]) -> Any:
        trajectory = extract_trajectory(request)
        if not trajectory:
            return None
        diagnosis = self.service.diagnose(trajectory)
        if diagnosis.risk_score < self.config.min_score_to_flag:
            return None
        mapped = map_diagnosis(diagnosis)
        mapped["report"] = self.report.build(diagnosis)
        return mapped

    def on_after_policy_decision(
        self, decision: GuardDecision, context: dict[str, Any]
    ) -> GuardDecision:
        diag = (context.get("plugin_results") or {}).get("agentdog")
        if diag:
            decision.metadata.setdefault("agentdog", diag.get("diagnosis"))
        return decision
