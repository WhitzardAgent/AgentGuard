"""ToolRepairSkill: repair a malformed parsed tool call."""
from __future__ import annotations

from agentguard.parser.repair import repair_tool_call
from agentguard.schemas.tool import ToolCall
from skills.base import BaseSkill, SkillInput, SkillOutput


class ToolRepairSkill(BaseSkill):
    name = "tool_repair"
    description = "Repair structural issues in a parsed tool call."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        data = input.data or {}
        tc = data.get("tool_call") or {}
        call = ToolCall(
            tool_name=tc.get("tool_name", ""),
            arguments=tc.get("arguments") or {},
            call_id=tc.get("call_id"),
            source_format=tc.get("source_format", "unknown"),
        )
        result = repair_tool_call(
            call,
            known_tools=data.get("known_tools"),
            required_args=data.get("required_args"),
        )
        return SkillOutput(
            result.success,
            {
                "tool_call": result.tool_call.to_dict() if result.tool_call else None,
                "warnings": result.warnings,
            },
            explanation=result.explanation,
            warnings=result.warnings,
        )
