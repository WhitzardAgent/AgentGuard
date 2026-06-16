"""Helpers for normalizing and merging checker configs by scope."""
from __future__ import annotations

import copy
from typing import Any

PHASE_ORDER = ("llm_before", "llm_after", "tool_before", "tool_after", "global")


def normalize_checker_config(
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError("checker config must be a JSON object")
    phases = config.get("phases")
    if not isinstance(phases, dict):
        raise ValueError("checker config must contain a 'phases' object")

    normalized: dict[str, dict[str, list[Any]]] = {}
    ordered_phases = list(PHASE_ORDER)
    ordered_phases.extend(
        phase for phase in phases.keys() if phase not in PHASE_ORDER
    )
    for phase in ordered_phases:
        if phase not in phases:
            continue
        normalized_phase = normalize_phase_config(phases.get(phase))
        if normalized_phase["local"] or normalized_phase["remote"]:
            normalized[str(phase)] = normalized_phase
    return {"phases": normalized}


def normalize_phase_config(value: Any) -> dict[str, list[Any]]:
    if not isinstance(value, dict):
        raise ValueError("checker phase config must be an object with 'local' and 'remote'")
    if "local" not in value or "remote" not in value:
        raise ValueError("checker phase config must include both 'local' and 'remote'")
    return {
        "local": _normalize_phase_specs(value.get("local"), "local"),
        "remote": _normalize_phase_specs(value.get("remote"), "remote"),
    }


def merge_checker_configs(
    remote_config: dict[str, Any] | None,
    client_config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized_remote = normalize_checker_config(remote_config)
    normalized_client = normalize_checker_config(client_config)
    if normalized_remote is None and normalized_client is None:
        return None

    remote_phases = dict((normalized_remote or {}).get("phases") or {})
    client_phases = dict((normalized_client or {}).get("phases") or {})
    ordered_phases = list(PHASE_ORDER)
    ordered_phases.extend(
        phase
        for phase in [*remote_phases.keys(), *client_phases.keys()]
        if phase not in PHASE_ORDER
    )

    merged: dict[str, dict[str, list[Any]]] = {}
    for phase in ordered_phases:
        if phase not in remote_phases and phase not in client_phases:
            continue
        remote_phase = normalize_phase_config(remote_phases.get(phase) or {"local": [], "remote": []})
        client_phase = normalize_phase_config(client_phases.get(phase) or {"local": [], "remote": []})
        local_specs = copy.deepcopy(
            client_phase["local"] if phase in client_phases else remote_phase["local"]
        )
        remote_specs = copy.deepcopy(
            remote_phase["remote"] if phase in remote_phases else client_phase["remote"]
        )
        if local_specs or remote_specs:
            merged[str(phase)] = {
                "local": local_specs,
                "remote": remote_specs,
            }
    return {"phases": merged}


def _normalize_phase_specs(value: Any, scope: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"checker phase '{scope}' config must be a list")
    return copy.deepcopy(list(value))
