"""Base plugin interface and result type for server-side checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeEvent


@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def empty() -> "CheckResult":
        return CheckResult()


class BasePlugin:
    """Server-side local plugin for one or more event types."""

    name: str = "base"
    description: str = ""
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        raise NotImplementedError
