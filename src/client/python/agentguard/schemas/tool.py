"""Normalized tool-call schema produced by the parser."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A normalized tool/function call parsed from LLM output."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None
    raw: Any = None
    source_format: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "call_id": self.call_id,
            "source_format": self.source_format,
        }


@dataclass
class ParseResult:
    """Result of parsing an LLM output into tool calls."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    malformed: bool = False
    repaired: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.tool_calls) and not self.malformed
