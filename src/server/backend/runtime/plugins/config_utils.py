"""Helpers for normalizing, merging, and enriching plugin configs by scope."""
from __future__ import annotations

import copy
from typing import Any

PHASE_ORDER = ("llm_before", "llm_after", "tool_before", "tool_after", "global")


def normalize_plugin_config(
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError("plugin config must be a JSON object")
    phases = config.get("phases")
    if not isinstance(phases, dict):
        raise ValueError("plugin config must contain a 'phases' object")

    normalized: dict[str, dict[str, list[Any]]] = {}
    ordered_phases = list(PHASE_ORDER)
    ordered_phases.extend(phase for phase in phases.keys() if phase not in PHASE_ORDER)
    for phase in ordered_phases:
        if phase not in phases:
            continue
        normalized_phase = normalize_phase_config(phases.get(phase))
        if normalized_phase["client"] or normalized_phase["server"]:
            normalized[phase] = normalized_phase
    return {"phases": normalized}


def normalize_phase_config(value: Any) -> dict[str, list[Any]]:
    if value is None:
        return {"client": [], "server": []}
    if not isinstance(value, dict):
        raise ValueError("plugin phase config must be an object with 'client' and 'server'")
    client = value.get("client", value.get("local"))
    server = value.get("server", value.get("remote"))
    if client is None:
        client = []
    if server is None:
        server = []
    if not isinstance(client, list) or not isinstance(server, list):
        raise ValueError("plugin phase config must include list-valued 'client' and 'server'")
    return {
        "client": copy.deepcopy(client),
        "server": copy.deepcopy(server),
    }


def merge_plugin_configs(
    remote_config: dict[str, Any] | None,
    client_config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized_remote = normalize_plugin_config(remote_config)
    normalized_client = normalize_plugin_config(client_config)
    if normalized_remote is None and normalized_client is None:
        return None

    phases: dict[str, dict[str, list[Any]]] = {}
    ordered_phases = list(PHASE_ORDER)
    for config in (normalized_remote, normalized_client):
        if not isinstance(config, dict):
            continue
        for phase in config.get("phases", {}):
            if phase not in ordered_phases:
                ordered_phases.append(phase)

    for phase in ordered_phases:
        client_specs = _phase_specs(normalized_client, phase, "client")
        server_specs = _phase_specs(normalized_remote, phase, "server")
        if client_specs or server_specs:
            phases[phase] = {
                "client": client_specs,
                "server": server_specs,
            }
    return {"phases": phases}


def hydrate_plugin_config(
    config: dict[str, Any] | None,
    *base_configs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized = normalize_plugin_config(config)
    if normalized is None:
        return None

    ordered_phases = list(PHASE_ORDER)
    for base in base_configs:
        normalized_base = normalize_plugin_config(base)
        if not isinstance(normalized_base, dict):
            continue
        for phase in normalized_base.get("phases", {}):
            if phase not in ordered_phases:
                ordered_phases.append(phase)

    specs_by_scope = {
        "client": _spec_index(base_configs, "client"),
        "server": _spec_index(base_configs, "server"),
    }
    phases: dict[str, dict[str, list[Any]]] = {}
    for phase in ordered_phases:
        phase_config = normalized.get("phases", {}).get(phase)
        if not isinstance(phase_config, dict):
            continue
        client_specs = [
            _hydrate_plugin_spec(spec, specs_by_scope["client"].get(_plugin_name(spec)))
            for spec in phase_config.get("client", [])
        ]
        server_specs = [
            _hydrate_plugin_spec(spec, specs_by_scope["server"].get(_plugin_name(spec)))
            for spec in phase_config.get("server", [])
        ]
        if client_specs or server_specs:
            phases[phase] = {
                "client": client_specs,
                "server": server_specs,
            }
    return {"phases": phases}


def _phase_specs(
    config: dict[str, Any] | None,
    phase: str,
    scope: str,
) -> list[Any]:
    if not isinstance(config, dict):
        return []
    phases = config.get("phases")
    if not isinstance(phases, dict):
        return []
    phase_config = phases.get(phase)
    if not isinstance(phase_config, dict):
        return []
    specs = phase_config.get(scope)
    return copy.deepcopy(specs) if isinstance(specs, list) else []


def _spec_index(
    configs: tuple[dict[str, Any] | None, ...],
    scope: str,
) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for raw in configs:
        config = normalize_plugin_config(raw)
        if not isinstance(config, dict):
            continue
        for phase in config.get("phases", {}).values():
            if not isinstance(phase, dict):
                continue
            specs = phase.get(scope)
            if not isinstance(specs, list):
                continue
            for spec in specs:
                name = _plugin_name(spec)
                if name and name not in indexed:
                    indexed[name] = copy.deepcopy(spec)
    return indexed


def _plugin_name(spec: Any) -> str:
    if isinstance(spec, str):
        return str(spec).strip()
    if isinstance(spec, dict):
        for key in ("name", "plugin", "class"):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _hydrate_plugin_spec(spec: Any, base_spec: Any) -> Any:
    if base_spec is None:
        return copy.deepcopy(spec)
    if isinstance(spec, str):
        return copy.deepcopy(base_spec)
    if isinstance(spec, dict) and isinstance(base_spec, dict):
        merged = copy.deepcopy(base_spec)
        for key, value in spec.items():
            if (
                key in {"env", "kwargs", "params"}
                and isinstance(merged.get(key), dict)
                and isinstance(value, dict)
            ):
                merged[key] = {
                    **copy.deepcopy(merged.get(key) or {}),
                    **copy.deepcopy(value),
                }
                continue
            merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(spec)
