"""Detect schema validity issues for a tool/skill schema."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import BaseDetector, DetectionResult


class SchemaDetector(BaseDetector):
    object_type = "schema"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        schema = obj.get("schema") or obj
        labels: list[str] = []
        if not schema.get("properties"):
            labels.append("no_properties")
        if schema.get("type") not in ("object", None):
            labels.append("non_object_root")
        return DetectionResult(
            object_id=obj.get("id", "schema"),
            object_type=self.object_type,
            name=obj.get("name", "schema"),
            risk_labels=labels,
            risk_level="low" if not labels else "medium",
            metadata={"valid": not labels},
        )
