"""Detector manager: dispatch objects to the right detector."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import DetectionResult
from backend.preprocess.detectors.mcp_detector import MCPDetector
from backend.preprocess.detectors.mcp_llm_detector import MCPLLMDetector
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
            "mcp_llm": MCPLLMDetector(),
            "policy": PolicyDetector(),
            "trace": TraceDetector(),
            "schema": SchemaDetector(),
        }

    def detect(self, object_type: str, obj: dict[str, Any]) -> DetectionResult:
        detector = self._detectors.get(object_type)
        if detector is None:
            raise ValueError(f"no detector for object type: {object_type}")
        if object_type == "mcp_llm":
            from backend.console.mcp_record import McpRecord

            record = McpRecord.from_descriptor(
                agent_id=str(obj.get("agent_id") or obj.get("owner_agent_id") or "").strip(),
                user_id=_optional_string(obj.get("user_id")),
                session_id=_optional_string(obj.get("session_id")),
                descriptor=obj,
            )
            if record is None:
                raise ValueError("mcp_llm detector requires agent_id and mcp name")
            return detector.detect(
                record,
                llm_config=obj.get("llm_config") if isinstance(obj.get("llm_config"), dict) else None,
            )
        return detector.detect(obj)

    def detect_trace(self, trace: dict[str, Any]) -> DetectionResult:
        return self._detectors["trace"].detect(trace)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
