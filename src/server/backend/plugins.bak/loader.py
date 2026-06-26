"""Load built-in server plugins into a manager."""
from __future__ import annotations

from backend.plugins.manager import PluginManager


def load_builtin_plugins(manager: PluginManager) -> PluginManager:
    return manager
