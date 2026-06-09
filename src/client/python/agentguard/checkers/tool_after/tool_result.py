"""Checker for tool result events (observation injection)."""
from __future__ import annotations

from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.checkers.patterns import find_signals, text_of
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


class ToolResultChecker(BaseChecker):
    name = "tool_result"
    event_types = [EventType.TOOL_RESULT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.get("result"))
        signals = find_signals(text)
        if "prompt_injection" in signals:
            signals.append("tool_result_injection")
        return CheckResult(risk_signals=sorted(set(signals)))
