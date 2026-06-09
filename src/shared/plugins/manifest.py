"""Shared plugin manifest schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    client_component: str | None = None
    server_component: str | None = None
    requires_server: bool = False
    supports_online: bool = True
    supports_offline: bool = False
    required_event_types: list[str] = field(default_factory=list)
    request_extensions: list[str] = field(default_factory=list)
    response_extensions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "client_component": self.client_component,
            "server_component": self.server_component,
            "requires_server": self.requires_server,
            "supports_online": self.supports_online,
            "supports_offline": self.supports_offline,
            "required_event_types": list(self.required_event_types),
            "request_extensions": list(self.request_extensions),
            "response_extensions": list(self.response_extensions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        return cls(
            plugin_id=data["plugin_id"],
            name=data["name"],
            version=data.get("version", "0.0.0"),
            client_component=data.get("client_component"),
            server_component=data.get("server_component"),
            requires_server=bool(data.get("requires_server", False)),
            supports_online=bool(data.get("supports_online", True)),
            supports_offline=bool(data.get("supports_offline", False)),
            required_event_types=list(data.get("required_event_types") or []),
            request_extensions=list(data.get("request_extensions") or []),
            response_extensions=list(data.get("response_extensions") or []),
        )
