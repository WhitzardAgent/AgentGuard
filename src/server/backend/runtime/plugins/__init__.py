"""Server-side plugins kept in parity with the client plugin layout."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.manager import PluginManager
from backend.runtime.plugins.registry import (
    get_plugin_class,
    plugin_descriptions,
    register,
    registered_plugins,
)


def server_plugin_manager(config: str | Path | dict[str, Any] | None = None) -> PluginManager:
    return PluginManager(config=config)


__all__ = [
    "server_plugin_manager",
    "PluginManager",
    "BasePlugin",
    "CheckResult",
    "register",
    "get_plugin_class",
    "registered_plugins",
    "plugin_descriptions",
]
