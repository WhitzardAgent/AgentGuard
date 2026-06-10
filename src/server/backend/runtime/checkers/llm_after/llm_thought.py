"""Deprecated checker for removed LLM thought events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult


class LLMThoughtChecker(BaseChecker):
    name = "llm_thought"
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
