"""Server plugin system."""
from __future__ import annotations

from backend.plugins.base import ServerPlugin
from backend.plugins.loader import load_builtin_plugins
from backend.plugins.manager import PluginManager
from backend.plugins.registry import PluginRegistry

__all__ = ["ServerPlugin", "PluginManager", "PluginRegistry", "load_builtin_plugins"]
