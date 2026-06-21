"""Tool registry mapping names to callables and metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agentguard.tools.metadata import ToolMetadata


@dataclass
class RegisteredTool:
    fn: Callable[..., Any]
    metadata: ToolMetadata


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        fn: Callable[..., Any],
        metadata: ToolMetadata | None = None,
        **overrides: Any,
    ) -> ToolMetadata:
        meta = metadata or ToolMetadata.infer(fn, **overrides)
        self._tools[meta.name] = RegisteredTool(fn=fn, metadata=meta)
        return meta

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def metadata(self, name: str) -> ToolMetadata | None:
        t = self._tools.get(name)
        return t.metadata if t else None

    def __contains__(self, name: str) -> bool:
        return name in self._tools
