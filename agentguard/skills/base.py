"""Skill base class, result type and registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from agentguard.schemas.context import RuntimeContext

log = logging.getLogger("agentguard.skills")


class SkillResult(BaseModel):
    skill: str
    ok: bool = True
    output: Any = None
    degraded: bool = False
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Skill(ABC):
    """Reusable reasoning module.

    Subclasses declare ``name`` and ``input_schema`` (a mapping of required
    input names to a short description) and implement :meth:`run`. If execution
    is blocked by policy or raises, :meth:`fallback` supplies a degraded result.
    """

    name: str = "skill"
    input_schema: dict[str, str] = {}

    def validate_inputs(self, inputs: dict[str, Any]) -> None:
        missing = [k for k in self.input_schema if k not in inputs]
        if missing:
            raise ValueError(f"skill '{self.name}' missing inputs: {missing}")

    @abstractmethod
    def run(self, context: RuntimeContext, **inputs: Any) -> Any:
        """Core reasoning logic; return the skill output."""
        raise NotImplementedError

    def fallback(self, context: RuntimeContext, reason: str, **inputs: Any) -> Any:
        """Degraded behaviour when :meth:`run` cannot proceed."""
        return None

    def execute(self, context: RuntimeContext, **inputs: Any) -> SkillResult:
        try:
            self.validate_inputs(inputs)
            output = self.run(context, **inputs)
            return SkillResult(skill=self.name, ok=True, output=output)
        except Exception as exc:  # noqa: BLE001
            log.warning("skill '%s' failed (%s); using fallback", self.name, exc)
            degraded = self.fallback(context, reason=str(exc), **inputs)
            return SkillResult(
                skill=self.name,
                ok=False,
                degraded=True,
                output=degraded,
                reason=str(exc),
            )


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills
