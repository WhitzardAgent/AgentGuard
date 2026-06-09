"""PolicyGapAnalyzerSkill: find tool capabilities not covered by any rule."""
from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillInput, SkillOutput


class PolicyGapAnalyzerSkill(BaseSkill):
    name = "policy_gap_analyzer"
    description = "Compare tool/skill metadata against existing policies."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        data = input.data or {}
        tools = data.get("tools") or []
        rules = data.get("rules") or []

        covered_caps: set[str] = set()
        for r in rules:
            covered_caps.update(r.get("capabilities") or [])

        gaps: list[dict[str, Any]] = []
        for tool in tools:
            caps = set(tool.get("capabilities") or [])
            uncovered = sorted(caps - covered_caps)
            if uncovered:
                gaps.append({"tool": tool.get("name"), "uncovered_capabilities": uncovered})

        return SkillOutput(
            True,
            {"gaps": gaps, "covered_capabilities": sorted(covered_caps)},
            explanation=f"{len(gaps)} tools have uncovered capabilities",
            warnings=[f"{g['tool']} uncovered: {g['uncovered_capabilities']}" for g in gaps],
        )
