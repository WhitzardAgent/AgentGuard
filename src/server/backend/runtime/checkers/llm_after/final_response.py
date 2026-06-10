"""Deprecated checker for removed final response events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult


class FinalResponseChecker(BaseChecker):
    name = "final_response"
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
