"""In-memory execution trace for a session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent


@dataclass
class TraceEntry:
    event: RuntimeEvent
    decision: GuardDecision | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "decision": self.decision.to_dict() if self.decision else None,
        }


@dataclass
class Trace:
    """Ordered list of events and their decisions for one session."""

    session_id: str
    entries: list[TraceEntry] = field(default_factory=list)

    def add(self, event: RuntimeEvent, decision: GuardDecision | None = None) -> None:
        self.entries.append(TraceEntry(event=event, decision=decision))

    def window(self, size: int) -> list[RuntimeEvent]:
        """Return the last `size` events (the trajectory window)."""
        return [e.event for e in self.entries[-size:]] if size > 0 else []

    def events(self) -> list[RuntimeEvent]:
        return [e.event for e in self.entries]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "entries": [e.to_dict() for e in self.entries],
        }
