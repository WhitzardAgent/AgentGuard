"""Detect targets and labels for a policy rule."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import BaseDetector, DetectionResult


class PolicyDetector(BaseDetector):
    object_type = "policy"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        rid = obj.get("rule_id", "rule")
        effect = obj.get("effect", "log_only")
        targets = list(obj.get("event_types") or [])
        risk = "high" if effect in ("deny", "require_approval") else "low"
        return DetectionResult(
            object_id=rid,
            object_type=self.object_type,
            name=rid,
            capabilities=list(obj.get("capabilities") or []),
            risk_labels=list(obj.get("risk_signals") or []),
            policy_targets=targets,
            risk_level=risk,
            metadata={"effect": effect, "priority": obj.get("priority", 0)},
        )
