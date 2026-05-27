"""Tiny heuristic risk scorer. MVP only -- replace with a model later."""

from __future__ import annotations

from typing import Any

from agentguard.models.events import RuntimeEvent


_SINK_RISK = {
    "none": 0.0,
    "email": 0.4,
    "http": 0.5,
    "shell": 0.7,
    "fs_write": 0.5,
    "db_write": 0.5,
    "llm_out": 0.3,
}


class RiskScorer:
    def score(
        self,
        event: RuntimeEvent,
        features: dict[str, Any],
        matched: list[str],
    ) -> float:
        risk = 0.0
        if event.tool_call is not None:
            risk = max(risk, _SINK_RISK.get(event.tool_call.sink_type, 0.0))
        if event.provenance_refs:
            labels = {r.label for r in event.provenance_refs}
            if any(lbl.startswith(("pii", "finance", "hr", "secret")) for lbl in labels):
                risk = max(risk, 0.8)
        if matched:
            risk = min(1.0, risk + 0.1 * len(matched))
        return round(risk, 3)
