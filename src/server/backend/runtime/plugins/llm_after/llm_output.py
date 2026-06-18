"""Plugin for LLM output events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.common.patterns import find_signals, text_of
from backend.runtime.plugins.registry import register


@register(
    name="llm_output",
    description="Detect risky content, secrets, and injection patterns in LLM output.",
)
class LLMOutputPlugin(BasePlugin):
    event_types = [EventType.LLM_OUTPUT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        text = text_of(event.payload.output)
        return CheckResult(risk_signals=find_signals(text))
