"""Deprecated plugin for removed LLM thought events."""
from __future__ import annotations

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.events import RuntimeEvent


@register(
    name="llm_thought",
    description="Deprecated no-op plugin for removed LLM thought events.",
)
class LLMThoughtPlugin(BasePlugin):
    event_types = []

    def applies(self, event: RuntimeEvent) -> bool:
        return False

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        return CheckResult.empty()
