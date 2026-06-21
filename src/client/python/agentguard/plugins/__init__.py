"""Local risk plugins."""
from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.manager import PluginManager, default_plugins
from agentguard.plugins.registry import (
    get_plugin_class,
    plugin_descriptions,
    register,
    registered_plugins,
)
from agentguard.plugins.llm_after import LLMOutputPlugin
from agentguard.plugins.llm_before import LLMInputPlugin
from agentguard.plugins.tool_after import ToolResultPlugin
from agentguard.plugins.tool_before import ToolInvokePlugin

__all__ = [
    "BasePlugin",
    "CheckResult",
    "PluginManager",
    "default_plugins",
    "register",
    "get_plugin_class",
    "registered_plugins",
    "plugin_descriptions",
    "LLMInputPlugin",
    "LLMOutputPlugin",
    "ToolInvokePlugin",
    "ToolResultPlugin",
]
