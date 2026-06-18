"""Server plugin manager: phased plugin execution."""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

from shared.schemas.context import RuntimeContext
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
            config[phase] = _plugin_specs_for_scope(phases.get(phase), "remote")
    return config


def _plugin_specs_for_scope(value: Any, scope: str) -> list[Any]:
    if not isinstance(value, dict):
        raise ValueError("plugin phase config must be an object with 'local' and 'remote'")
    if "local" not in value or "remote" not in value:
        raise ValueError("plugin phase config must include both 'local' and 'remote'")
    specs = value.get(scope)
    if specs is None:
        return []
    if not isinstance(specs, list):
        raise ValueError(f"plugin phase '{scope}' config must be a list")
    return list(specs)


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
        is_final = False
        meta: dict[str, Any] = {}
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
            for signal in res.risk_signals:
                if signal not in merged_signals:
                    merged_signals.append(signal)
                event.add_signal(signal)
            if res.metadata:
                meta.update(res.metadata)
            if res.decision_candidate and (candidate is None or res.is_final):
                candidate = res.decision_candidate
                is_final = is_final or res.is_final
                if stop_on_first_decision:
                    break

        for signal in merged_signals:
            event.add_signal(signal)
        return CheckResult(
            decision_candidate=candidate,
            risk_signals=merged_signals,
            is_final=is_final,
            metadata=meta,
        )


def _instantiate_plugin(spec: Any) -> BasePlugin:
    if isinstance(spec, BasePlugin):
        return spec
    if isinstance(spec, type) and issubclass(spec, BasePlugin):
        return spec()
    if isinstance(spec, str):
        cls = get_plugin_class(spec) or _load_plugin_class(spec)
        return cls()
    if isinstance(spec, dict):
        target = spec.get("class") or spec.get("plugin") or spec.get("name")
        if isinstance(target, str):
            cls = get_plugin_class(target) or _load_plugin_class(target)
        elif isinstance(target, type) and issubclass(target, BasePlugin):
            cls = target
        else:
            raise ValueError(f"invalid plugin config entry: {spec!r}")
        return cls()
    raise ValueError(f"invalid plugin config entry: {spec!r}")


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
