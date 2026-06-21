"""Plugin for tool result events (observation injection)."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.common.patterns import find_signals, text_of
from backend.runtime.plugins.registry import register


@register(
    name="tool_result",
    description="Detect secrets and prompt-injection content in tool results.",
)
class ToolResultPlugin(BasePlugin):
    event_types = [EventType.TOOL_RESULT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        text = text_of(event.payload.result)
        signals = find_signals(text)
        if "prompt_injection" in signals:
            signals.append("tool_result_injection")
        return CheckResult(risk_signals=sorted(set(signals)))
