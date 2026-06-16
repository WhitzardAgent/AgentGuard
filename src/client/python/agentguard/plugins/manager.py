"""Checker manager: run applicable checkers and merge results."""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

from agentguard.plugins.base import BaseChecker, CheckResult
from agentguard.plugins.registry import get_checker_class
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent

PHASE_ORDER = ("llm_before", "llm_after", "tool_before", "tool_after", "global")

_EVENT_PHASE = {
    EventType.LLM_INPUT: "llm_before",
    EventType.LLM_OUTPUT: "llm_after",
    EventType.TOOL_INVOKE: "tool_before",
    EventType.TOOL_RESULT: "tool_after",
}

def default_checkers() -> list[BaseChecker]:
    return []


def default_checker_config() -> dict[str, dict[str, list[Any]]]:
    return {}


def load_checker_config(source: str | Path | dict[str, Any] | None) -> dict[str, list[Any]]:
    if source is None:
        return {}
    elif isinstance(source, (str, Path)):
        path = Path(source)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = dict(source)

    phases = data.get("phases")
    if not isinstance(phases, dict):
        raise ValueError("checker config must contain a 'phases' object")
    config: dict[str, list[Any]] = {}
    for phase in PHASE_ORDER:
        if phase in phases:
            config[phase] = _checker_specs_for_scope(phases.get(phase), "local")
    return config


def _checker_specs_for_scope(value: Any, scope: str) -> list[Any]:
    if not isinstance(value, dict):
        raise ValueError("checker phase config must be an object with 'local' and 'remote'")
    if "local" not in value or "remote" not in value:
        raise ValueError("checker phase config must include both 'local' and 'remote'")
    specs = value.get(scope)
    if specs is None:
        return []
    if not isinstance(specs, list):
        raise ValueError(f"checker phase '{scope}' config must be a list")
    return list(specs)


def build_checkers_by_phase(config: dict[str, list[Any]]) -> dict[str, list[BaseChecker]]:
    return {
        phase: [_instantiate_checker(spec) for spec in specs]
        for phase, specs in config.items()
    }


def _instantiate_checker(spec: Any) -> BaseChecker:
    if isinstance(spec, BaseChecker):
        return spec
    if isinstance(spec, type) and issubclass(spec, BaseChecker):
        return _build_checker(spec)
    if isinstance(spec, str):
        cls = get_checker_class(spec) or _load_checker_class(spec)
        return _build_checker(cls)
    if isinstance(spec, dict):
        target = spec.get("class") or spec.get("checker") or spec.get("name")
        kwargs = _checker_kwargs(spec)
        env = _checker_env(spec)
        if isinstance(target, str):
            cls = get_checker_class(target) or _load_checker_class(target)
        elif isinstance(target, type) and issubclass(target, BaseChecker):
            cls = target
        else:
            raise ValueError(f"invalid checker config entry: {spec!r}")
        return _build_checker(cls, kwargs=kwargs, env=env)
    raise ValueError(f"invalid checker config entry: {spec!r}")


def _checker_kwargs(spec: dict[str, Any]) -> dict[str, Any]:
    reserved = {"class", "checker", "name", "kwargs", "env"}
    kwargs = {key: value for key, value in spec.items() if key not in reserved}
    explicit_kwargs = spec.get("kwargs") or {}
    if not isinstance(explicit_kwargs, dict):
        raise ValueError(f"checker kwargs config must be an object: {spec!r}")
    kwargs.update(explicit_kwargs)
    return kwargs


def _checker_env(spec: dict[str, Any]) -> dict[str, Any]:
    env = spec.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError(f"checker env config must be an object: {spec!r}")
    return dict(env)


def _build_checker(
    cls: type[BaseChecker],
    *,
    kwargs: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> BaseChecker:
    checker_kwargs = dict(kwargs or {})
    checker_env = dict(env or {})
    if _accepts_env_kwarg(cls):
        return cls(env=checker_env, **checker_kwargs)
    checker = cls(**checker_kwargs)
    checker.bind_config(env=checker_env, **checker_kwargs)
    return checker


def _accepts_env_kwarg(cls: type[BaseChecker]) -> bool:
    try:
        params = inspect.signature(cls.__init__).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params) or any(
        param.name == "env" for param in params
    )


def _load_checker_class(path: str) -> type[BaseChecker]:
    module_name, _, class_name = path.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"checker must be a builtin name or import path: {path}")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not isinstance(cls, type) or not issubclass(cls, BaseChecker):
        raise TypeError(f"checker class must subclass BaseChecker: {path}")
    return cls


class CheckerManager:
    """Runs all applicable checkers and merges their CheckResults."""

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
        self._refresh_flat_checkers()

    def update_config(self, config: str | Path | dict[str, Any] | None) -> None:
        """Replace checker configuration for subsequent events."""
        self.checkers_by_phase = build_checkers_by_phase(load_checker_config(config))
        self._refresh_flat_checkers()

    def add(self, checker: BaseChecker, phase: str | None = None) -> None:
        target = phase or _infer_phase(checker)
        self.checkers_by_phase.setdefault(target, []).append(checker)
        self.checkers.append(checker)

    def _refresh_flat_checkers(self) -> None:
        self.checkers = [
            checker
            for phase in PHASE_ORDER
            for checker in self.checkers_by_phase.get(phase, [])
        ]

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


def _infer_phase(checker: BaseChecker) -> str:
    for event_type in checker.event_types:
        phase = _EVENT_PHASE.get(event_type)
        if phase:
            return phase
    return "global"
