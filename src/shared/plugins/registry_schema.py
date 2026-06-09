"""Schema for a registry of plugin manifests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.plugins.manifest import PluginManifest


@dataclass
class PluginRegistrySchema:
    plugins: list[PluginManifest] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"plugins": [p.to_dict() for p in self.plugins]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginRegistrySchema":
        return cls(plugins=[PluginManifest.from_dict(p) for p in data.get("plugins") or []])

    def by_id(self, plugin_id: str) -> PluginManifest | None:
        for p in self.plugins:
            if p.plugin_id == plugin_id:
                return p
        return None
