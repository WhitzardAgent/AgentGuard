"""Run root-level skills locally."""
from __future__ import annotations

from typing import Any

from agentguard.utils.errors import SkillError


class LocalSkillRunner:
    """Resolve and run skills from the project-level `skills` registry."""

    def __init__(self) -> None:
        self._registry = None

    def _load(self):  # lazy import; skills package lives at repo root
        if self._registry is None:
            try:
                from skills.registry import get_registry  # noqa: PLC0415
            except Exception as exc:  # pragma: no cover - environment dependent
                raise SkillError(f"skills package unavailable: {exc}") from exc
            self._registry = get_registry()
        return self._registry

    def run(self, skill_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        registry = self._load()
        skill = registry.get(skill_name)
        if skill is None:
            raise SkillError(f"unknown skill: {skill_name}")
        from skills.base import SkillInput  # noqa: PLC0415

        si = SkillInput(
            instruction=input_data.get("instruction"),
            data=input_data.get("data") or {},
            context=input_data.get("context") or {},
        )
        out = skill.run(si)
        return out.to_dict() if hasattr(out, "to_dict") else dict(out)

    def list_skills(self) -> list[str]:
        return self._load().names()
