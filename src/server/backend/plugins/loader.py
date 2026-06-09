"""Load built-in server plugins into a manager."""
from __future__ import annotations

from backend.plugins.manager import PluginManager


def load_builtin_plugins(manager: PluginManager, *, enable_agentdog: bool = True) -> PluginManager:
    if enable_agentdog:
        from backend.plugins.builtin.agentdog.plugin import AgentDoGServerPlugin  # noqa: PLC0415

        manager.register(AgentDoGServerPlugin())
    return manager
