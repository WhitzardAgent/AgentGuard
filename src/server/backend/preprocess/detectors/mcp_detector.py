"""Detect labels for an MCP tool/server descriptor."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import BaseDetector, DetectionResult
from backend.preprocess.labels.capability import infer_capabilities


class MCPDetector(BaseDetector):
    object_type = "mcp"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        name = obj.get("name", "mcp_tool")
        caps = list(obj.get("capabilities") or []) or infer_capabilities(name)
        remote = bool(obj.get("remote", True))
        risk = "high" if remote and caps else "medium"
        labels = ["remote_mcp"] if remote else []
        return DetectionResult(
            object_id=obj.get("id", name),
            object_type=self.object_type,
            name=name,
            capabilities=caps,
            risk_labels=labels,
            policy_targets=["tool_invoke"],
            suggested_plugins=["tool_invoke", "tool_result"],
            risk_level=risk,
            metadata={"remote": remote},
        )
