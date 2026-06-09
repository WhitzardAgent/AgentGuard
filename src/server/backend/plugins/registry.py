"""Registry of server plugins."""
from __future__ import annotations

from backend.plugins.base import ServerPlugin


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ServerPlugin] = {}

    def add(self, plugin: ServerPlugin) -> None:
        self._plugins[plugin.plugin_id] = plugin

    def all(self) -> list[ServerPlugin]:
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> ServerPlugin | None:
        return self._plugins.get(plugin_id)
