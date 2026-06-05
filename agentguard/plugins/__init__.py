"""Dynamic plugin architecture.

Plugins are modules that extend the Harness at runtime without modifying core
code. A plugin is either:

* a module exposing a module-level ``register(guard)`` function, or
* a class subclassing :class:`Plugin` (auto-discovered in the module).

Plugins may register new middleware, skills, policy rules, event subscribers or
lifecycle hooks through the :class:`~agentguard.AgentGuard` facade passed to
``register``.
"""

from agentguard.plugins.manager import Plugin, PluginManager

__all__ = ["Plugin", "PluginManager"]
