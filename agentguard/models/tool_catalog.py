"""Static tool-catalog models used by the remote runtime control plane."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field


class ToolCatalogLabels(BaseModel):
    boundary: str = "internal"
    sensitivity: str = "low"
    integrity: str = "trusted"
    tags: list[str] = Field(default_factory=list)


class ToolCatalogEntry(BaseModel):
    owner_agent_id: str
    name: str
    labels: ToolCatalogLabels = Field(default_factory=ToolCatalogLabels)
    input_params: list[str] = Field(default_factory=list)
    updated_at_ms: int | None = None

    def with_updated_timestamp(self, ts_ms: int | None = None) -> "ToolCatalogEntry":
        return self.model_copy(
            update={"updated_at_ms": ts_ms if ts_ms is not None else int(time.time() * 1000)}
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "owner_agent_id": self.owner_agent_id,
            "name": self.name,
            "labels": self.labels.model_dump(mode="json"),
            "input_params": list(self.input_params),
        }
