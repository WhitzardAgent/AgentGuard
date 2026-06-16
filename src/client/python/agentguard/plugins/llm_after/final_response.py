"""Deprecated checker for removed final response events."""
from __future__ import annotations

from agentguard.plugins.base import BaseChecker, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


@register(
    name="final_response",
    description="Deprecated no-op checker for removed final response events.",
)
class FinalResponseChecker(BaseChecker):
    event_types = []

    def applies(self, event: RuntimeEvent) -> bool:
        return False

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        return CheckResult.empty()
