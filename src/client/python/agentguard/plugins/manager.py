"""Plugin manager: wire plugin hooks into the runtime lifecycle."""
from __future__ import annotations

from agentguard.harness.lifecycle import Lifecycle
from agentguard.plugins.base import ClientPlugin
from agentguard.plugins.protocol import NOTIFY_HOOKS, TRANSFORM_HOOKS
from agentguard.plugins.registry import PluginRegistry
from agentguard.schemas.context import RuntimeContext


class PluginManager:
    def __init__(self, lifecycle: Lifecycle) -> None:
        self.lifecycle = lifecycle
        self.registry = PluginRegistry()

    def register(self, plugin: ClientPlugin) -> ClientPlugin:
        self.registry.add(plugin)
        for hook in TRANSFORM_HOOKS:
            fn = getattr(plugin, hook, None)
            if callable(fn):
                self.lifecycle.register(hook, fn)
        for hook in NOTIFY_HOOKS:
            fn = getattr(plugin, hook, None)
            if callable(fn):
                self.lifecycle.register(hook, fn)
        return plugin

    def start_session(self, context: RuntimeContext) -> None:
        self.lifecycle.notify("on_session_start", context)

    def end_session(self, trace: object, context: RuntimeContext) -> None:
        self.lifecycle.notify("on_session_end", trace, context)

    def plugins(self) -> list[ClientPlugin]:
        return self.registry.all()
