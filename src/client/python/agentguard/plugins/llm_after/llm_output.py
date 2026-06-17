"""Checker for LLM output events."""
from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.common.patterns import find_signals, text_of
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="llm_output",
    description="Detect risky content, secrets, and injection patterns in LLM output.",
)
class LLMOutputChecker(BasePlugin):
    event_types = [EventType.LLM_OUTPUT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.get("output"))
        return CheckResult(risk_signals=find_signals(text))
