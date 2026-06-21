"""Skill base interfaces shared by client and server."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillInput:
    instruction: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillOutput:
    success: bool
    result: dict[str, Any]
    explanation: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "explanation": self.explanation,
            "warnings": list(self.warnings),
            "metadata": self.metadata,
        }


class BaseSkill:
    name: str = "base"
    description: str = ""

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002 - matches spec
        raise NotImplementedError
