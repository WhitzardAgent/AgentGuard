"""Workflow-level degradation: insert an approval/checkpoint step."""
from __future__ import annotations

from typing import Any


def degrade_workflow(tool_name: str, reason: str) -> dict[str, Any]:
    return {
        "type": "workflow",
        "insert_step": "human_approval",
        "blocked_tool": tool_name,
        "explanation": f"workflow degraded: {reason}",
    }
