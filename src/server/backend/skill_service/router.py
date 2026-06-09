"""Skill service entry used by the API layer."""
from __future__ import annotations

from typing import Any

from backend.skill_service.runner import SkillRunner


class SkillServiceRouter:
    def __init__(self, runner: SkillRunner | None = None) -> None:
        self.runner = runner or SkillRunner()

    def run(self, body: dict[str, Any]) -> dict[str, Any]:
        skill_name = body.get("skill_name")
        if not skill_name:
            return {"success": False, "result": {}, "explanation": "missing skill_name"}
        return self.runner.run(skill_name, body.get("input") or {})

    def list_skills(self) -> list[str]:
        return self.runner.registry.names()
