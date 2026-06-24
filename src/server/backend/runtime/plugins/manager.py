"""Server plugin manager: phased plugin execution."""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import EventType, RuntimeEvent

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import get_plugin_class

PHASE_ORDER = ("llm_before", "llm_after", "tool_before", "tool_after", "global")

_EVENT_PHASE = {
    EventType.LLM_INPUT: "llm_before",
    EventType.LLM_OUTPUT: "llm_after",
    EventType.TOOL_INVOKE: "tool_before",
    EventType.TOOL_RESULT: "tool_after",
}

_DECISION_RANK = {
    DecisionType.ALLOW: 0,
    DecisionType.LOG_ONLY: 1,
    DecisionType.ALIGN_THOUGHT: 2,
    DecisionType.LOOP_BACK_TO_LLM: 3,
    DecisionType.REPAIR: 4,
    DecisionType.REWRITE: 5,
    DecisionType.SANITIZE: 6,
    DecisionType.DEGRADE: 7,
    DecisionType.HUMAN_CHECK: 8,
    DecisionType.REQUIRE_APPROVAL: 8,
    DecisionType.REQUIRE_REMOTE_REVIEW: 9,
    DecisionType.DROP_THOUGHT: 9,
    DecisionType.DENY: 10,
}


def default_plugins() -> list[BasePlugin]:
    return []


def default_plugin_config() -> dict[str, dict[str, list[Any]]]:
    return {}


def load_plugin_config(source: str | Path | dict[str, Any] | None) -> dict[str, list[Any]]:
    if source is None:
        return {}
    if isinstance(source, (str, Path)):
        path = Path(source)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = dict(source)

    phases = data.get("phases")
    if not isinstance(phases, dict):
        raise ValueError("plugin config must contain a 'phases' object")
    config: dict[str, list[Any]] = {}
    for phase in PHASE_ORDER:
        if phase in phases:
            config[phase] = _plugin_specs_for_scope(phases.get(phase), "server")
    return config


def _plugin_specs_for_scope(value: Any, scope: str) -> list[Any]:
    if not isinstance(value, dict):
        raise ValueError("plugin phase config must be an object with 'client' and 'server'")
    if not _has_scope(value, "client") or not _has_scope(value, "server"):
        raise ValueError("plugin phase config must include both 'client' and 'server'")
    specs = _scope_value(value, scope)
    if specs is None:
        return []
    if not isinstance(specs, list):
        raise ValueError(f"plugin phase '{scope}' config must be a list")
    return list(specs)


def _has_scope(value: dict[str, Any], scope: str) -> bool:
    return scope in value or _legacy_scope(scope) in value


def _scope_value(value: dict[str, Any], scope: str) -> Any:
    if scope in value:
        return value.get(scope)
    return value.get(_legacy_scope(scope))


def _legacy_scope(scope: str) -> str:
    return "local" if scope == "client" else "remote"


def build_plugins_by_phase(config: dict[str, list[Any]]) -> dict[str, list[BasePlugin]]:
    return {
        phase: [_instantiate_plugin(spec) for spec in specs]
        for phase, specs in config.items()
    }


class PluginManager:
    """Runs configured plugins for the event phase and merges CheckResults."""

    def __init__(
        self,
        plugins: list[BasePlugin] | None = None,
        *,
        config: str | Path | dict[str, Any] | None = None,
    ) -> None:
        if plugins is not None:
            self.plugins_by_phase = {"global": list(plugins)}
        else:
            self.plugins_by_phase = build_plugins_by_phase(load_plugin_config(config))
        self._refresh_flat_plugins()

    def update_config(self, config: str | Path | dict[str, Any] | None) -> None:
        """Replace plugin configuration for subsequent server decisions."""
        self.plugins_by_phase = build_plugins_by_phase(load_plugin_config(config))
        self._refresh_flat_plugins()

    def _refresh_flat_plugins(self) -> None:
        self.plugins = [
            plugin
            for phase in PHASE_ORDER
            for plugin in self.plugins_by_phase.get(phase, [])
        ]

    def add(self, plugin: BasePlugin, phase: str | None = None) -> None:
        target = phase or _infer_phase(plugin)
        self.plugins_by_phase.setdefault(target, []).append(plugin)
        self.plugins.append(plugin)

    def run(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        *,
        trajectory_window: list[RuntimeEvent] | None = None,
        stop_on_first_decision: bool = False,
    ) -> CheckResult:
        merged_signals: list[str] = []
        candidate = None
        candidate_is_final = False
        meta: dict[str, Any] = {}
        plugin_outcomes: list[dict[str, Any]] = []
        selected_outcome_index: int | None = None
        phase = _EVENT_PHASE.get(event.event_type, "global")
        phase_plugins = list(self.plugins_by_phase.get(phase, []))
        phase_plugins.extend(self.plugins_by_phase.get("global", []))
        for plugin in phase_plugins:
            if not plugin.applies(event):
                continue
            try:
                res = _call_plugin(plugin, event, context, trajectory_window)
            except Exception as exc:
                meta[f"{plugin.name}_error"] = str(exc)
                continue
            plugin_outcomes.append(_plugin_outcome_dict(plugin, res))
            for signal in res.risk_signals:
                if signal not in merged_signals:
                    merged_signals.append(signal)
                event.add_signal(signal)
            if res.metadata:
                meta.update(res.metadata)
            if res.decision_candidate and _should_replace_decision(
                current=candidate,
                current_is_final=candidate_is_final,
                incoming=res.decision_candidate,
                incoming_is_final=res.is_final,
            ):
                candidate = res.decision_candidate
                candidate_is_final = res.is_final
                selected_outcome_index = len(plugin_outcomes) - 1
                if stop_on_first_decision and _should_stop_on_decision(candidate, candidate_is_final):
                    break

        for signal in merged_signals:
            event.add_signal(signal)
        meta["plugin_outcomes"] = plugin_outcomes
        if selected_outcome_index is not None:
            meta["selected_outcome_index"] = selected_outcome_index
        return CheckResult(
            decision_candidate=candidate,
            risk_signals=merged_signals,
            is_final=candidate_is_final,
            metadata=meta,
        )


def _instantiate_plugin(spec: Any) -> BasePlugin:
    if isinstance(spec, BasePlugin):
        return spec
    if isinstance(spec, type) and issubclass(spec, BasePlugin):
        return _build_plugin(spec)
    if isinstance(spec, str):
        cls = get_plugin_class(spec) or _load_plugin_class(spec)
        return _build_plugin(cls)
    if isinstance(spec, dict):
        target = spec.get("class") or spec.get("plugin") or spec.get("name")
        kwargs = _plugin_kwargs(spec)
        env = _plugin_env(spec)
        if isinstance(target, str):
            cls = get_plugin_class(target) or _load_plugin_class(target)
        elif isinstance(target, type) and issubclass(target, BasePlugin):
            cls = target
        else:
            raise ValueError(f"invalid plugin config entry: {spec!r}")
        return _build_plugin(cls, kwargs=kwargs, env=env)
    raise ValueError(f"invalid plugin config entry: {spec!r}")


def _plugin_kwargs(spec: dict[str, Any]) -> dict[str, Any]:
    reserved = {"class", "plugin", "name", "kwargs", "env"}
    kwargs = {key: value for key, value in spec.items() if key not in reserved}
    explicit_kwargs = spec.get("kwargs") or {}
    if not isinstance(explicit_kwargs, dict):
        raise ValueError(f"plugin kwargs config must be an object: {spec!r}")
    kwargs.update(explicit_kwargs)
    return kwargs


def _plugin_env(spec: dict[str, Any]) -> dict[str, Any]:
    env = spec.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError(f"plugin env config must be an object: {spec!r}")
    return dict(env)


def _build_plugin(
    cls: type[BasePlugin],
    *,
    kwargs: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> BasePlugin:
    plugin_kwargs = dict(kwargs or {})
    plugin_env = dict(env or {})
    if _accepts_env_kwarg(cls):
        return cls(env=plugin_env, **plugin_kwargs)
    plugin = cls(**plugin_kwargs)
    plugin.bind_config(env=plugin_env, **plugin_kwargs)
    return plugin


def _accepts_env_kwarg(cls: type[BasePlugin]) -> bool:
    try:
        params = inspect.signature(cls.__init__).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params) or any(
        param.name == "env" for param in params
    )


def _call_plugin(
    plugin: BasePlugin,
    event: RuntimeEvent,
    context: RuntimeContext,
    trajectory_window: list[RuntimeEvent] | None,
) -> CheckResult:
    """Call new trace-aware plugins while tolerating old two-arg plugins."""
    params = inspect.signature(plugin.check).parameters
    if len(params) >= 3:
        return plugin.check(event, context, trajectory_window)
    return plugin.check(event, context)  # type: ignore[call-arg]


def _plugin_outcome_dict(plugin: BasePlugin, res: CheckResult) -> dict[str, Any]:
    return {
        "plugin": plugin.name,
        "decision_candidate": (
            res.decision_candidate.to_dict() if res.decision_candidate is not None else None
        ),
        "risk_signals": list(res.risk_signals),
        "is_final": bool(res.is_final),
        "metadata": dict(res.metadata),
    }


def _decision_rank(decision: GuardDecision | None) -> int:
    if decision is None:
        return -1
    return _DECISION_RANK.get(decision.decision_type, -1)


def _should_replace_decision(
    *,
    current: GuardDecision | None,
    current_is_final: bool,
    incoming: GuardDecision,
    incoming_is_final: bool,
) -> bool:
    if current is None:
        return True
    incoming_rank = _decision_rank(incoming)
    current_rank = _decision_rank(current)
    if incoming_rank != current_rank:
        return incoming_rank > current_rank
    if incoming_is_final != current_is_final:
        return incoming_is_final
    return False


def _should_stop_on_decision(decision: GuardDecision | None, is_final: bool) -> bool:
    return bool(
        decision is not None
        and is_final
        and decision.decision_type == DecisionType.DENY
    )


def _load_plugin_class(path: str) -> type[BasePlugin]:
    module_name, _, class_name = path.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"plugin must be a builtin name or import path: {path}")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not isinstance(cls, type) or not issubclass(cls, BasePlugin):
        raise TypeError(f"plugin class must subclass BasePlugin: {path}")
    return cls


def _infer_phase(plugin: BasePlugin) -> str:
    for event_type in plugin.event_types:
        phase = _EVENT_PHASE.get(event_type)
        if phase:
            return phase
    return "global"
