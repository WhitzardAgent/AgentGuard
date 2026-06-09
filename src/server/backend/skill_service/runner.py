"""Run project skills on the server."""
from __future__ import annotations

from typing import Any

from backend.skill_service.registry import SkillRegistry


class SkillRunner:
    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def run(self, skill_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        skill = self.registry.get(skill_name)
        if skill is None:
            return {"success": False, "result": {}, "explanation": f"unknown skill: {skill_name}"}
        from skills.base import SkillInput  # noqa: PLC0415

        si = SkillInput(
            instruction=input_data.get("instruction"),
            data=input_data.get("data") or {},
            context=input_data.get("context") or {},
        )
        out = skill.run(si)
        return out.to_dict() if hasattr(out, "to_dict") else dict(out)
