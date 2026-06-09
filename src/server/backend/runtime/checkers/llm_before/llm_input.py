"""Checker for user/LLM input events."""
from __future__ import annotations

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.patterns import find_signals, text_of


class LLMInputChecker(BaseChecker):
    name = "llm_input"
    event_types = [EventType.USER_INPUT, EventType.LLM_INPUT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.get("text") or event.payload.get("messages"))
        signals = [s for s in find_signals(text) if s in {"prompt_injection", "system_prompt_leak"}]
        return CheckResult(risk_signals=signals)
