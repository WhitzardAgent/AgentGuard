"""Client plugin system."""
from __future__ import annotations

from agentguard.plugins.base import ClientPlugin
from agentguard.plugins.manager import PluginManager
from agentguard.plugins.registry import PluginRegistry

__all__ = [
    "ClientPlugin",
    "PluginManager",
    "PluginRegistry",
]
