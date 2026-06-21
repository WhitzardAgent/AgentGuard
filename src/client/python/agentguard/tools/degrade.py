"""Policy-aware tool degradation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Default safe degradation map.
DEFAULT_DEGRADE_MAP = {
    "send_email": "draft_email",
    "delete_file": "move_to_trash",
    "run_shell": "explain_command",
    "external_post": "local_summary",
    "network_write": "draft_request",
}


@dataclass
class DegradePlan:
    degraded: bool
    target_tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    safe_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "degraded": self.degraded,
            "target_tool": self.target_tool,
            "arguments": self.arguments,
            "explanation": self.explanation,
            "safe_error": self.safe_error,
        }


class ToolDegradeManager:
    """Maps risky tools to safe alternatives."""

    def __init__(self, mapping: dict[str, str] | None = None, available: set[str] | None = None) -> None:
        self.mapping = dict(DEFAULT_DEGRADE_MAP)
        if mapping:
            self.mapping.update(mapping)
        self.available = available if available is not None else set(self.mapping.values())

    def plan(
        self, tool_name: str, arguments: dict[str, Any], reason: str = ""
    ) -> DegradePlan:
        target = self.mapping.get(tool_name)
        if not target:
            return DegradePlan(
                degraded=False,
                safe_error=f"No safe degradation for '{tool_name}'; action blocked.",
                explanation=reason or "no degradation mapping",
            )
        if target not in self.available:
            return DegradePlan(
                degraded=False,
                target_tool=target,
                safe_error=f"Degraded tool '{target}' unavailable; action blocked.",
                explanation=reason or "degraded tool unavailable",
            )
        return DegradePlan(
            degraded=True,
            target_tool=target,
            arguments=dict(arguments),
            explanation=reason or f"degraded {tool_name} -> {target}",
        )
