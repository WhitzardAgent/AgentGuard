"""Skill registry with a lazily-initialized default singleton."""
from __future__ import annotations

from skills.base import BaseSkill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> BaseSkill:
        self._skills[skill.name] = skill
        return skill

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def all(self) -> list[BaseSkill]:
        return list(self._skills.values())

    def __contains__(self, name: str) -> bool:
        return name in self._skills


_REGISTRY: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        from skills.loader import load_default_skills  # noqa: PLC0415

        _REGISTRY = SkillRegistry()
        load_default_skills(_REGISTRY)
    return _REGISTRY
