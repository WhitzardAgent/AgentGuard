"""Detect labels for a skill definition."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import BaseDetector, DetectionResult


class SkillDetector(BaseDetector):
    object_type = "skill"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        name = obj.get("name", "skill")
        category = obj.get("category", "developer")
        risk = "low" if category == "developer" else "medium"
        return DetectionResult(
            object_id=obj.get("id", name),
            object_type=self.object_type,
            name=name,
            risk_labels=[],
            policy_targets=["skill_run"],
            suggested_checkers=[],
            risk_level=risk,
            metadata={"category": category},
        )
