"""Deprecated plugin for removed final response events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register


@register(
    name="final_response",
    description="Deprecated no-op plugin for removed final response events.",
)
class FinalResponsePlugin(BasePlugin):
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
