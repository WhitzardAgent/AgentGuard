"""Generic tool-registry middleware.

Any framework exposing a dict-like tool registry can call
`ToolMiddleware.install(registry)` to wrap every registered tool.
"""

from __future__ import annotations

from typing import Any, MutableMapping, TYPE_CHECKING

from agentguard.sdk.wrappers import wrap_tool

if TYPE_CHECKING:
    from agentguard.sdk.guard import Guard


class ToolMiddleware:
    def __init__(self, guard: "Guard") -> None:
        self._guard = guard

    def install(self, registry: MutableMapping[str, Any]) -> None:
        for name, fn in list(registry.items()):
            if not callable(fn) or getattr(fn, "__agentguard__", None):
                continue
            registry[name] = wrap_tool(self._guard, name, fn)
            self._guard._record_tool_registration(name, registry[name])
