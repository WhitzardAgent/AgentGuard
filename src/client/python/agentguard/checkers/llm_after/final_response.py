"""Deprecated checker for removed final response events."""
from __future__ import annotations

from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class FinalResponseChecker(BaseChecker):
    name = "final_response"
    event_types = []

    def applies(self, event: RuntimeEvent) -> bool:
        return False

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        return CheckResult.empty()
