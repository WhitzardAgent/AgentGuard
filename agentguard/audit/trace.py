"""In-memory execution trace grouping events + decisions by session."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentguard.schemas.decision import Decision
from agentguard.schemas.events import RuntimeEvent
from agentguard.utils.time import now_ms


class TraceSpan(BaseModel):
    """One intercepted behaviour together with the decision that was made."""

    seq: int
    ts_ms: int = Field(default_factory=now_ms)
    event: RuntimeEvent
    decision: Decision | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts_ms": self.ts_ms,
            "event": self.event.summary(),
            "type": self.event.type.value,
            "action": self.decision.action.value if self.decision else None,
            "reason": self.decision.reason if self.decision else None,
            "risk": self.decision.risk_score if self.decision else None,
        }


class Trace:
    """Ordered collection of :class:`TraceSpan` for a single session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._spans: list[TraceSpan] = []

    def add(self, event: RuntimeEvent, decision: Decision | None = None) -> TraceSpan:
        span = TraceSpan(seq=len(self._spans), event=event, decision=decision)
        self._spans.append(span)
        return span

    @property
    def spans(self) -> list[TraceSpan]:
        return list(self._spans)

    def rows(self) -> list[dict[str, Any]]:
        return [s.as_row() for s in self._spans]

    def __len__(self) -> int:
        return len(self._spans)
