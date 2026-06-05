"""Normalized runtime events intercepted by the Harness (PEP).

Every agent runtime behaviour — tool calls, tool arguments, observations,
memory writes, file operations, network actions, LLM thoughts and final
responses — is normalized into a single :class:`RuntimeEvent` so that policy
evaluation, middleware analysis and auditing all operate on one shape.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Taxonomy of behaviours the Harness intercepts and normalizes."""

    # Tool / action lifecycle
    TOOL_CALL = "tool_call"
    TOOL_ARGS = "tool_args"
    TOOL_OBSERVATION = "tool_observation"

    # Memory / storage
    MEMORY_WRITE = "memory_write"
    MEMORY_READ = "memory_read"

    # Side-effecting resources
    FILE_OP = "file_op"
    NETWORK_ACTION = "network_action"

    # LLM reasoning
    LLM_THOUGHT = "llm_thought"
    LLM_PROMPT = "llm_prompt"
    FINAL_RESPONSE = "final_response"

    # Skills / plugins
    SKILL_INVOKED = "skill_invoked"
    SKILL_RESULT = "skill_result"

    # Lifecycle
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"


class RuntimeEvent(BaseModel):
    """A single normalized runtime behaviour flowing through the Harness."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    type: EventType

    session_id: str
    user_id: str | None = None
    agent_id: str | None = None

    # Tool-flavoured fields (populated for TOOL_* events)
    tool_name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    sink_type: str = "none"

    # Free-text content (populated for LLM_THOUGHT / FINAL_RESPONSE / observations)
    content: str | None = None

    # Arbitrary structured payload + analyzer annotations
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)

    def annotate(self, key: str, value: Any) -> "RuntimeEvent":
        """Attach a middleware annotation in place and return self (chainable)."""
        self.annotations[key] = value
        return self

    def with_content(self, content: str) -> "RuntimeEvent":
        return self.model_copy(update={"content": content})

    def with_args(self, args: dict[str, Any]) -> "RuntimeEvent":
        return self.model_copy(update={"args": dict(args)})

    def summary(self) -> str:
        """Short human-readable description for audit logs."""
        if self.tool_name:
            return f"{self.type.value}:{self.tool_name}"
        if self.content:
            preview = self.content[:48].replace("\n", " ")
            return f"{self.type.value}:{preview}"
        return self.type.value
