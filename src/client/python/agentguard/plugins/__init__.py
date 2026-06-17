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
from agentguard.plugins.llm_after import LLMOutputChecker
from agentguard.plugins.llm_before import LLMInputChecker
from agentguard.plugins.tool_after import ToolResultChecker
from agentguard.plugins.tool_before import ToolInvokeChecker

__all__ = [
    "BasePlugin",
    "CheckResult",
    "PluginManager",
    "default_plugins",
    "register",
    "get_plugin_class",
    "registered_plugins",
    "plugin_descriptions",
    "LLMInputChecker",
    "LLMOutputChecker",
    "ToolInvokeChecker",
    "ToolResultChecker",
]
