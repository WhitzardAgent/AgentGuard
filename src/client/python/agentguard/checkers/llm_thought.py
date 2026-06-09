"""Checker for LLM internal thought events."""
from __future__ import annotations

from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.checkers.patterns import find_signals, text_of
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent

_UNSAFE_INTENT = (
    "exfiltrate",
    "bypass the policy",
    "ignore the guard",
    "hide this from",
    "without permission",
    "secretly",
)


class LLMThoughtChecker(BaseChecker):
    name = "llm_thought"
    event_types = [EventType.LLM_THOUGHT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.get("thought"))
        signals = find_signals(text)
        low = text.lower()
        if any(p in low for p in _UNSAFE_INTENT):
            signals.append("unsafe_thought")
        return CheckResult(risk_signals=signals)
