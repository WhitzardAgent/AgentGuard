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
