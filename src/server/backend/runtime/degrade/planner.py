"""Degrade planner: produce a structured, policy-compliant degradation plan."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.runtime.degrade.argument_degrader import degrade_arguments
from backend.runtime.degrade.tool_degrader import degrade_tool
from backend.runtime.degrade.workflow_degrader import degrade_workflow


@dataclass
class DegradePlan:
    level: str  # "tool" | "argument" | "workflow"
    target_tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "target_tool": self.target_tool,
            "arguments": self.arguments,
            "workflow": self.workflow,
            "explanation": self.explanation,
        }


class DegradePlanner:
    def plan(
        self, tool_name: str, arguments: dict[str, Any], reason: str = ""
    ) -> DegradePlan:
        target = degrade_tool(tool_name)
        if target:
            return DegradePlan(
                level="tool",
                target_tool=target,
                arguments=dict(arguments),
                explanation=f"degrade {tool_name} -> {target}: {reason}",
            )
        arg_plan = degrade_arguments(arguments)
        if arg_plan["removed"]:
            return DegradePlan(
                level="argument",
                target_tool=tool_name,
                arguments=arg_plan["arguments"],
                explanation=f"neutralized sinks {arg_plan['removed']}: {reason}",
            )
        return DegradePlan(
            level="workflow",
            target_tool=tool_name,
            workflow=degrade_workflow(tool_name, reason),
            explanation=f"workflow degradation for {tool_name}: {reason}",
        )
