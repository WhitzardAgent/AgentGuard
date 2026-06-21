"""Normalized LLM request/response schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content, "metadata": self.metadata}


@dataclass
class LLMRequest:
    """Provider-agnostic LLM request."""

    messages: list[LLMMessage] = field(default_factory=list)
    model: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [m.to_dict() for m in self.messages],
            "model": self.model,
            "tools": self.tools,
            "params": self.params,
        }


@dataclass
class LLMResponse:
    """Provider-agnostic LLM response."""

    text: str | None = None
    thought: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "thought": self.thought,
            "tool_calls": self.tool_calls,
            "metadata": self.metadata,
        }
