"""Checker for LLM output events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.common.patterns import find_signals, text_of


class LLMOutputChecker(BaseChecker):
    name = "llm_output"
    event_types = [EventType.LLM_OUTPUT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        text = text_of(event.payload.get("output"))
        return CheckResult(risk_signals=find_signals(text))
