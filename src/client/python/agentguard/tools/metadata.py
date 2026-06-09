"""Tool metadata for registration and policy targeting."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolMetadata:
    name: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    required_args: list[str] = field(default_factory=list)
    degraded_to: str | None = None
    is_async: bool = False
    schema: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "required_args": list(self.required_args),
            "degraded_to": self.degraded_to,
            "is_async": self.is_async,
            "schema": self.schema,
            "metadata": self.metadata,
        }

    @classmethod
    def infer(cls, fn: Callable[..., Any], **overrides: Any) -> "ToolMetadata":
        name = overrides.pop("name", None) or getattr(fn, "__name__", "tool")
        doc = overrides.pop("description", None) or (inspect.getdoc(fn) or "")
        is_async = inspect.iscoroutinefunction(fn)
        required = []
        try:
            sig = inspect.signature(fn)
            required = [
                p.name
                for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            ]
        except (TypeError, ValueError):
            pass
        return cls(
            name=name,
            description=doc.split("\n")[0] if doc else "",
            required_args=overrides.pop("required_args", required),
            is_async=is_async,
            **overrides,
        )
