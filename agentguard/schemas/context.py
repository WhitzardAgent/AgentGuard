"""Runtime context carried alongside every intercepted event."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class RuntimeContext(BaseModel):
    """Identity, policy and scope information for the current agent run.

    A single context object is created when a :class:`~agentguard.AgentGuard`
    session starts and is threaded through the event bus, middleware, PEP and
    audit subsystems.
    """

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str | None = None
    agent_id: str | None = None

    policy: str = "default"
    goal: str | None = None
    scope: list[str] = Field(default_factory=list)

    sandboxed: bool = True
    fail_open: bool = True

    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def child(self, **overrides: Any) -> "RuntimeContext":
        """Derive a sub-context (e.g. for a spawned sub-agent or skill)."""
        data = self.model_dump()
        data.update(overrides)
        return RuntimeContext(**data)
