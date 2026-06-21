"""Detector manager: dispatch objects to the right detector."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import DetectionResult
from backend.preprocess.detectors.mcp_detector import MCPDetector
from backend.preprocess.detectors.policy_detector import PolicyDetector
from backend.preprocess.detectors.schema_detector import SchemaDetector
from backend.preprocess.detectors.skill_detector import SkillDetector
from backend.preprocess.detectors.tool_detector import ToolDetector
from backend.preprocess.detectors.trace_detector import TraceDetector


class DetectorManager:
    def __init__(self) -> None:
        self._detectors = {
            "tool": ToolDetector(),
            "skill": SkillDetector(),
            "mcp": MCPDetector(),
            "policy": PolicyDetector(),
            "trace": TraceDetector(),
            "schema": SchemaDetector(),
        }

    def detect(self, object_type: str, obj: dict[str, Any]) -> DetectionResult:
        detector = self._detectors.get(object_type)
        if detector is None:
            raise ValueError(f"no detector for object type: {object_type}")
        return detector.detect(obj)

    def detect_trace(self, trace: dict[str, Any]) -> DetectionResult:
        return self._detectors["trace"].detect(trace)
