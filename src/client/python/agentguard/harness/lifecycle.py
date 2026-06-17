"""Lifecycle hook registry invoked by the runtime."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

# Known lifecycle hook names.
HOOKS = (
    "on_session_start",
    "on_event",
    "on_llm_input",
    "on_llm_output",
    "on_tool_invoke",
    "on_tool_result",
    "on_before_remote_decision",
    "on_after_remote_decision",
    "on_session_end",
)


class Lifecycle:
    """Registers and dispatches runtime lifecycle callbacks."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        if name not in HOOKS:
            raise ValueError(f"unknown lifecycle hook: {name}")
        self._hooks[name].append(fn)

    def dispatch(self, name: str, value: Any, *args: Any) -> Any:
        """Run hooks in order; each may transform and return `value`."""
        for fn in self._hooks.get(name, []):
            try:
                out = fn(value, *args)
                if out is not None:
                    value = out
            except Exception:  # hooks must not break the runtime
                continue
        return value

    def notify(self, name: str, *args: Any) -> None:
        for fn in self._hooks.get(name, []):
            try:
                fn(*args)
            except Exception:
                continue
