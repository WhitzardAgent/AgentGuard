"""Session state model (Instruction.md §3.1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from agentguard.models.events import Principal


@dataclass
class GuardSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    principal: Principal = field(
        default_factory=lambda: Principal(agent_id="unknown", session_id="unknown"))
    goal: str | None = None
    scope: list[str] = field(default_factory=list)
    registered_tools: list[str] = field(default_factory=list)
    risk_level: float = 0.0
    phase: str = "idle"  # idle | planning | acting | waiting | review
