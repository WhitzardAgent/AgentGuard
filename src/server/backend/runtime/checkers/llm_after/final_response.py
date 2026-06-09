"""Checker for final response events."""
from __future__ import annotations

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.patterns import find_signals, text_of


class FinalResponseChecker(BaseChecker):
    name = "final_response"
    event_types = [EventType.FINAL_RESPONSE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.get("text"))
        signals = find_signals(text)
        if {"secret_detected", "api_key_detected", "system_prompt_leak"} & set(signals):
            signals.append("unsafe_final_response")
        return CheckResult(risk_signals=sorted(set(signals)))
