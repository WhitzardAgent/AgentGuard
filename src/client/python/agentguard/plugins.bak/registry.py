"""Registry of active client plugins."""
from __future__ import annotations

from agentguard.plugins.base import ClientPlugin


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ClientPlugin] = {}

    def add(self, plugin: ClientPlugin) -> None:
        self._plugins[plugin.plugin_id] = plugin

    def get(self, plugin_id: str) -> ClientPlugin | None:
        return self._plugins.get(plugin_id)

    def all(self) -> list[ClientPlugin]:
        return list(self._plugins.values())

    def __contains__(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins
