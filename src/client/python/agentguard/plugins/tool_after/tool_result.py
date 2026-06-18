"""Plugin for tool result events (observation injection)."""
from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.common.patterns import find_signals, text_of
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="tool_result",
    description="Detect secrets and prompt-injection content in tool results.",
)
class ToolResultPlugin(BasePlugin):
    event_types = [EventType.TOOL_RESULT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.result)
        signals = find_signals(text)
        if "prompt_injection" in signals:
            signals.append("tool_result_injection")
        return CheckResult(risk_signals=sorted(set(signals)))
