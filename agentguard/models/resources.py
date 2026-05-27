"""Resource model used in execution graph and provenance tracking."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Resource(BaseModel):
    """A data resource referenced by tool calls."""

    res_id: str
    kind: str  # file / table / url / mem / ...
    labels: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
