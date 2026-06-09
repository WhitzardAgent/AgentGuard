"""Detect capabilities and risk for a tool definition."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import BaseDetector, DetectionResult
from backend.preprocess.labels.capability import infer_capabilities
from backend.preprocess.labels.risk import HIGH_RISK_SIGNALS

_CAP_CHECKER = {
    "external_send": "tool_invoke",
    "shell": "tool_invoke",
    "write_file": "tool_invoke",
    "database_write": "tool_invoke",
}


class ToolDetector(BaseDetector):
    object_type = "tool"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        name = obj.get("name", "tool")
        caps = list(obj.get("capabilities") or [])
        for c in infer_capabilities(name):
            if c not in caps:
                caps.append(c)
        high = {"external_send", "shell", "database_write", "payment"} & set(caps)
        risk_level = "high" if high else ("medium" if caps else "low")
        checkers = sorted({_CAP_CHECKER[c] for c in caps if c in _CAP_CHECKER})
        return DetectionResult(
            object_id=obj.get("id", name),
            object_type=self.object_type,
            name=name,
            capabilities=caps,
            risk_labels=sorted(high),
            policy_targets=["tool_invoke"],
            suggested_checkers=checkers or ["tool_invoke"],
            risk_level=risk_level,
            metadata={"high_risk_signals": sorted(HIGH_RISK_SIGNALS & set(caps))},
        )
