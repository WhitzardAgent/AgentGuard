"""Base checker interface and result type."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def empty() -> "CheckResult":
        return CheckResult()


class BaseChecker:
    """Local, non-networked risk checker for one or more event types."""

    name: str = "base"
    description: str = ""
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError
