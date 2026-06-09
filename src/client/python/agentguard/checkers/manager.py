"""Checker manager: run applicable checkers and merge results."""
from __future__ import annotations

from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.checkers.final_response import FinalResponseChecker
from agentguard.checkers.llm_input import LLMInputChecker
from agentguard.checkers.llm_output import LLMOutputChecker
from agentguard.checkers.llm_thought import LLMThoughtChecker
from agentguard.checkers.memory import MemoryChecker
from agentguard.checkers.tool_invoke import ToolInvokeChecker
from agentguard.checkers.tool_result import ToolResultChecker
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


def default_checkers() -> list[BaseChecker]:
    return [
        LLMInputChecker(),
        LLMOutputChecker(),
        LLMThoughtChecker(),
        ToolInvokeChecker(),
        ToolResultChecker(),
        FinalResponseChecker(),
        MemoryChecker(),
    ]


class CheckerManager:
    """Runs all applicable checkers and merges their CheckResults."""

    def __init__(self, checkers: list[BaseChecker] | None = None) -> None:
        self.checkers = checkers if checkers is not None else default_checkers()

    def add(self, checker: BaseChecker) -> None:
        self.checkers.append(checker)

    def run(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        merged_signals: list[str] = []
        candidate = None
        is_final = False
        meta: dict = {}
        for checker in self.checkers:
            if not checker.applies(event):
                continue
            try:
                res = checker.check(event, context)
            except Exception as exc:  # checkers must never break the flow
                meta[f"{checker.name}_error"] = str(exc)
                continue
            for s in res.risk_signals:
                if s not in merged_signals:
                    merged_signals.append(s)
            if res.metadata:
                meta.update(res.metadata)
            # Keep the strongest final candidate (first final wins).
            if res.decision_candidate and (candidate is None or res.is_final):
                candidate = res.decision_candidate
                is_final = is_final or res.is_final
        # Annotate the event with detected signals.
        for s in merged_signals:
            event.add_signal(s)
        return CheckResult(
            decision_candidate=candidate,
            risk_signals=merged_signals,
            is_final=is_final,
            metadata=meta,
        )
