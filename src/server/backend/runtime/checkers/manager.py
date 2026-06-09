"""Server checker manager: phased checker execution."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.llm_after import FinalResponseChecker, LLMOutputChecker, LLMThoughtChecker
from backend.runtime.checkers.llm_before import LLMInputChecker
from backend.runtime.checkers.memory import MemoryChecker
from backend.runtime.checkers.tool_after import ToolResultChecker
from backend.runtime.checkers.tool_before import ToolInvokeChecker

PHASE_ORDER = ("llm_before", "llm_after", "tool_before", "tool_after", "memory", "global")

_EVENT_PHASE = {
    EventType.USER_INPUT: "llm_before",
    EventType.LLM_INPUT: "llm_before",
    EventType.LLM_OUTPUT: "llm_after",
    EventType.LLM_THOUGHT: "llm_after",
    EventType.FINAL_RESPONSE: "llm_after",
    EventType.TOOL_INVOKE: "tool_before",
    EventType.TOOL_RESULT: "tool_after",
    EventType.MEMORY_READ: "memory",
    EventType.MEMORY_WRITE: "memory",
}

_BUILTIN_CHECKERS = {
    "llm_input": LLMInputChecker,
    "llm_output": LLMOutputChecker,
    "llm_thought": LLMThoughtChecker,
    "final_response": FinalResponseChecker,
    "tool_invoke": ToolInvokeChecker,
    "tool_result": ToolResultChecker,
    "memory": MemoryChecker,
}


def default_checkers() -> list[BaseChecker]:
    by_phase = build_checkers_by_phase(default_checker_config())
    return [checker for phase in PHASE_ORDER for checker in by_phase.get(phase, [])]


def default_checker_config() -> dict[str, list[Any]]:
    return {
        "llm_before": ["llm_input"],
        "llm_after": ["llm_output", "llm_thought", "final_response"],
        "tool_before": ["tool_invoke"],
        "tool_after": ["tool_result"],
        "memory": ["memory"],
    }


def load_checker_config(source: str | Path | dict[str, Any] | None) -> dict[str, list[Any]]:
    if source is None:
        return default_checker_config()
    if isinstance(source, (str, Path)):
        path = Path(source)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = dict(source)

    phases = data.get("phases", data)
    config: dict[str, list[Any]] = {}
    for phase in PHASE_ORDER:
        if phase in phases:
            config[phase] = list(phases.get(phase) or [])
    return config


def build_checkers_by_phase(config: dict[str, list[Any]]) -> dict[str, list[BaseChecker]]:
    return {
        phase: [_instantiate_checker(spec) for spec in specs]
        for phase, specs in config.items()
    }


class CheckerManager:
    """Runs configured checkers for the event phase and merges CheckResults."""

    def __init__(
        self,
        checkers: list[BaseChecker] | None = None,
        *,
        config: str | Path | dict[str, Any] | None = None,
    ) -> None:
        if checkers is not None:
            self.checkers_by_phase = {"global": list(checkers)}
        else:
            self.checkers_by_phase = build_checkers_by_phase(load_checker_config(config))
        self.checkers = [
            checker
            for phase in PHASE_ORDER
            for checker in self.checkers_by_phase.get(phase, [])
        ]

    def add(self, checker: BaseChecker, phase: str | None = None) -> None:
        target = phase or _infer_phase(checker)
        self.checkers_by_phase.setdefault(target, []).append(checker)
        self.checkers.append(checker)

    def run(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        merged_signals: list[str] = []
        candidate = None
        is_final = False
        meta: dict = {}
        phase = _EVENT_PHASE.get(event.event_type, "global")
        phase_checkers = list(self.checkers_by_phase.get(phase, []))
        phase_checkers.extend(self.checkers_by_phase.get("global", []))
        for checker in phase_checkers:
            if not checker.applies(event):
                continue
            try:
                res = checker.check(event, context)
            except Exception as exc:
                meta[f"{checker.name}_error"] = str(exc)
                continue
            for signal in res.risk_signals:
                if signal not in merged_signals:
                    merged_signals.append(signal)
            if res.metadata:
                meta.update(res.metadata)
            if res.decision_candidate and (candidate is None or res.is_final):
                candidate = res.decision_candidate
                is_final = is_final or res.is_final

        for signal in merged_signals:
            event.add_signal(signal)
        return CheckResult(
            decision_candidate=candidate,
            risk_signals=merged_signals,
            is_final=is_final,
            metadata=meta,
        )


def _instantiate_checker(spec: Any) -> BaseChecker:
    if isinstance(spec, BaseChecker):
        return spec
    if isinstance(spec, type) and issubclass(spec, BaseChecker):
        return spec()
    if isinstance(spec, str):
        cls = _BUILTIN_CHECKERS.get(spec) or _load_checker_class(spec)
        return cls()
    if isinstance(spec, dict):
        target = spec.get("class") or spec.get("checker") or spec.get("name")
        kwargs = dict(spec.get("kwargs") or {})
        if isinstance(target, str):
            cls = _BUILTIN_CHECKERS.get(target) or _load_checker_class(target)
        elif isinstance(target, type) and issubclass(target, BaseChecker):
            cls = target
        else:
            raise ValueError(f"invalid checker config entry: {spec!r}")
        return cls(**kwargs)
    raise ValueError(f"invalid checker config entry: {spec!r}")


def _load_checker_class(path: str) -> type[BaseChecker]:
    module_name, _, class_name = path.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"checker must be a builtin name or import path: {path}")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not isinstance(cls, type) or not issubclass(cls, BaseChecker):
        raise TypeError(f"checker class must subclass BaseChecker: {path}")
    return cls


def _infer_phase(checker: BaseChecker) -> str:
    for event_type in checker.event_types:
        phase = _EVENT_PHASE.get(event_type)
        if phase:
            return phase
    return "global"
