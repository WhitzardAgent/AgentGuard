"""Adapter for OpenClaw runtime."""

from __future__ import annotations

from typing import Any

from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.wrappers import wrap_tool


class OpenClawAdapter(BaseAdapter):
    def install(self, framework_obj: Any) -> None:
        tool_registry = getattr(framework_obj, "tool_registry", None)
        if tool_registry is None or not isinstance(tool_registry, dict):
            return
        for name, fn in list(tool_registry.items()):
            if not callable(fn) or getattr(fn, "__agentguard__", None):
                continue
            tool_registry[name] = wrap_tool(self.guard, name, fn)
            self.guard._record_tool_registration(name, tool_registry[name])
