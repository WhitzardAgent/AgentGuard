"""Server preprocess detectors."""
from __future__ import annotations

from backend.preprocess.detectors.base import BaseDetector, DetectionResult

__all__ = [
    "BaseDetector",
    "DetectionResult",
    "DetectorManager",
    "ToolDetector",
    "SkillDetector",
    "MCPDetector",
    "MCPLLMDetector",
    "PolicyDetector",
    "TraceDetector",
    "SchemaDetector",
]


def __getattr__(name: str):
    if name == "DetectorManager":
        from backend.preprocess.detectors.manager import DetectorManager

        return DetectorManager
    if name == "ToolDetector":
        from backend.preprocess.detectors.tool_detector import ToolDetector

        return ToolDetector
    if name == "SkillDetector":
        from backend.preprocess.detectors.skill_detector import SkillDetector

        return SkillDetector
    if name == "MCPDetector":
        from backend.preprocess.detectors.mcp_detector import MCPDetector

        return MCPDetector
    if name == "MCPLLMDetector":
        from backend.preprocess.detectors.mcp_llm_detector import MCPLLMDetector

        return MCPLLMDetector
    if name == "PolicyDetector":
        from backend.preprocess.detectors.policy_detector import PolicyDetector

        return PolicyDetector
    if name == "TraceDetector":
        from backend.preprocess.detectors.trace_detector import TraceDetector

        return TraceDetector
    if name == "SchemaDetector":
        from backend.preprocess.detectors.schema_detector import SchemaDetector

        return SchemaDetector
    raise AttributeError(name)
