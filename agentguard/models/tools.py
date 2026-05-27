"""Tool specification model (Instruction.md §3.7)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """Declarative tool description with degrade options."""

    name: str
    version: str = "v1"
    tags: list[str] = Field(default_factory=list)
    sink_type: str = "none"
    degrade_options: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
