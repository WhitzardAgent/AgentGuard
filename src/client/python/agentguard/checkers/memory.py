"""Checker for memory read/write events."""
from __future__ import annotations

from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.checkers.patterns import find_signals, text_of
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


class MemoryChecker(BaseChecker):
    name = "memory"
    event_types = [EventType.MEMORY_READ, EventType.MEMORY_WRITE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload)
        signals = find_signals(text)
        if event.event_type == EventType.MEMORY_WRITE and (
            {"secret_detected", "api_key_detected"} & set(signals)
        ):
            signals.append("memory_write_secret")
        return CheckResult(risk_signals=sorted(set(signals)))
