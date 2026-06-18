"""Helpers for normalizing and merging plugin configs by scope."""
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
        if normalized_phase["local"] or normalized_phase["remote"]:
            normalized[phase] = normalized_phase
    return {"phases": normalized}


def normalize_phase_config(value: Any) -> dict[str, list[Any]]:
    if value is None:
        return {"local": [], "remote": []}
    if not isinstance(value, dict):
        raise ValueError("plugin phase config must be an object with 'local' and 'remote'")
    local = value.get("local")
    remote = value.get("remote")
    if local is None:
        local = []
    if remote is None:
        remote = []
    if not isinstance(local, list) or not isinstance(remote, list):
        raise ValueError("plugin phase config must include list-valued 'local' and 'remote'")
    return {
        "local": copy.deepcopy(local),
        "remote": copy.deepcopy(remote),
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
        local_specs = _phase_specs(normalized_client, phase, "local")
        remote_specs = _phase_specs(normalized_remote, phase, "remote")
        if local_specs or remote_specs:
            phases[phase] = {
                "local": local_specs,
                "remote": remote_specs,
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
