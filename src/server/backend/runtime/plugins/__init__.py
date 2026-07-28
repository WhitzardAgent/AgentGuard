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
    manager = PluginManager(config=config)
    _attach_internal_rule_based_plugin(manager)
    return manager


def _attach_internal_rule_based_plugin(manager: PluginManager) -> None:
    from backend.runtime.plugins.tool_before.rule_based_plugin import RuleBasedPlugin

    phase_names = ("llm_before", "llm_after", "tool_before", "tool_after")
    existing = {
        phase: any(isinstance(plugin, RuleBasedPlugin) for plugin in manager.plugins_by_phase.get(phase, []))
        for phase in phase_names
    }
    for phase in phase_names:
        if existing.get(phase):
            continue
        manager.add(RuleBasedPlugin(), phase=phase)


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
