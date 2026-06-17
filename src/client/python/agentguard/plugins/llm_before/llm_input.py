"""Checker for user/LLM input events."""
from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.common.patterns import find_signals, text_of
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="llm_input",
    description="Detect prompt-injection and system-prompt leak attempts in LLM input.",
)
class LLMInputChecker(BasePlugin):
    event_types = [EventType.LLM_INPUT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.get("text") or event.payload.get("messages"))
        signals = [s for s in find_signals(text) if s in {"prompt_injection", "system_prompt_leak"}]
        return CheckResult(risk_signals=signals)
