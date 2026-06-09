"""Skill manifest schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillManifest:
    name: str
    description: str = ""
    category: str = "developer"
    version: str = "0.1.0"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }
