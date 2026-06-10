"""Checker for tool result events (observation injection)."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.common.patterns import find_signals, text_of


class ToolResultChecker(BaseChecker):
    name = "tool_result"
    event_types = [EventType.TOOL_RESULT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        text = text_of(event.payload.get("result"))
        signals = find_signals(text)
        if "prompt_injection" in signals:
            signals.append("tool_result_injection")
        return CheckResult(risk_signals=sorted(set(signals)))
