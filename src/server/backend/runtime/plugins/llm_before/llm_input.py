"""Plugin for user/LLM input events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.common.patterns import find_signals, text_of
from backend.runtime.plugins.registry import register


@register(
    name="llm_input",
    description="Detect prompt-injection and system-prompt leak attempts in LLM input.",
)
class LLMInputPlugin(BasePlugin):
    event_types = [EventType.LLM_INPUT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        text = text_of(event.payload.messages)
        signals = [s for s in find_signals(text) if s in {"prompt_injection", "system_prompt_leak"}]
        return CheckResult(risk_signals=signals)
