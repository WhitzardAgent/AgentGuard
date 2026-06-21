"""Guarded tool wrapper. Delegates the enforcement flow to the runtime."""
from __future__ import annotations

import functools
from typing import Any, Callable

from agentguard.tools.metadata import ToolMetadata


class ToolWrapper:
    """Callable wrapper that routes every invocation through the runtime."""

    def __init__(
        self,
        fn: Callable[..., Any],
        metadata: ToolMetadata,
        runtime: Any,
    ) -> None:
        self._fn = fn
        self.metadata = metadata
        self._runtime = runtime
        functools.update_wrapper(self, fn, updated=[])

    @property
    def name(self) -> str:
        return self.metadata.name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        arguments = self._bind(args, kwargs)
        return self._runtime.invoke_tool(
            tool_name=self.metadata.name,
            arguments=arguments,
            fn=self._fn,
            metadata=self.metadata,
        )

    def _bind(self, args: tuple, kwargs: dict) -> dict[str, Any]:
        """Map positional args to names using the original signature."""
        if not args:
            return dict(kwargs)
        import inspect

        try:
            sig = inspect.signature(self._fn)
            bound = sig.bind_partial(*args, **kwargs)
            return dict(bound.arguments)
        except (TypeError, ValueError):
            merged = dict(kwargs)
            merged["_args"] = list(args)
            return merged
