"""Deprecated checker for removed LLM thought events."""
from __future__ import annotations

from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class LLMThoughtChecker(BaseChecker):
    name = "llm_thought"
    event_types = []

    def applies(self, event: RuntimeEvent) -> bool:
        return False

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        return CheckResult.empty()
