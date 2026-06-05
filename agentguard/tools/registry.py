"""Registry mapping tool names to callables + metadata."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from agentguard.tools.metadata import ToolMetadata


@dataclass
class RegisteredTool:
    fn: Callable[..., Any]
    metadata: ToolMetadata

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        sink_type: str = "none",
        capabilities: list[str] | None = None,
        **meta: Any,
    ) -> RegisteredTool:
        tool_name = name or getattr(fn, "__name__", "tool")
        param_names = [
            p
            for p, spec in inspect.signature(fn).parameters.items()
            if spec.kind
            not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        metadata = ToolMetadata.build(
            tool_name,
            sink_type=sink_type,
            capabilities=capabilities,
            param_names=param_names,
            **meta,
        )
        registered = RegisteredTool(fn=fn, metadata=metadata)
        self._tools[tool_name] = registered
        return registered

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
