"""ArgumentDegradeSkill: degrade risky tool arguments to a safe draft."""
from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillInput, SkillOutput

# Argument keys to neutralize when degrading a side-effecting action.
_SINK_KEYS = ("to", "recipient", "url", "endpoint", "host", "channel")


class ArgumentDegradeSkill(BaseSkill):
    name = "argument_degrade"
    description = "Degrade side-effecting arguments into a safe draft."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        data = input.data or {}
        args: dict[str, Any] = dict(data.get("arguments") or {})
        degraded: dict[str, Any] = dict(args)
        removed = []
        for key in _SINK_KEYS:
            if key in degraded:
                removed.append(key)
                degraded[key] = None
        degraded["_mode"] = "draft"
        return SkillOutput(
            True,
            {"arguments": degraded, "removed_sinks": removed, "draft": True},
            explanation=f"degraded {len(removed)} side-effect arguments to draft mode",
        )
