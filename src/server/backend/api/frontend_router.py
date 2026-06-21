"""Frontend/admin API routes for plugin config and session management."""
from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    AgentPluginAvailableResponse,
    AgentPluginConfigResponse,
    AgentPluginConfigUpdateRequest,
    PluginConfigUpdateRequest,
    PluginConfigUpdateResponse,
    TraceAuditRequest,
    TraceAuditResponse,
)
from backend.app_state import get_console, get_manager
from backend.audit import auditor_descriptions, auditor_manager
from backend.runtime.plugins.config_utils import merge_plugin_configs
from backend.runtime.plugins.registry import registered_plugins as registered_server_plugins
from shared.schemas.events import EventType
from shared.utils.json import safe_dumps, safe_loads

router = APIRouter()

_manager = get_manager()
get_console()
_auditors = auditor_manager()

_EVENT_PHASE = {
    EventType.LLM_INPUT.value: "llm_before",
    EventType.LLM_OUTPUT.value: "llm_after",
    EventType.TOOL_INVOKE.value: "tool_before",
    EventType.TOOL_RESULT.value: "tool_after",
}
_KNOWN_PHASES = ("llm_before", "llm_after", "tool_before", "tool_after", "global")
_DEPRECATED_PLUGIN_NAMES = {"memory", "llm_thought", "final_response"}


@router.get("/v1/backend/sessions")
def list_sessions() -> dict[str, Any]:
    return {"sessions": _manager.session_pool.list()}


@router.post("/v1/backend/sessions/refresh-stale")
def refresh_stale_sessions() -> dict[str, Any]:
    return {"results": _manager.refresh_stale_sessions()}


@router.get("/v1/backend/sessions/{session_id}")
def get_session(
    session_id: str,
    agent_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    record = _manager.session_pool.get(session_id, agent_id=agent_id, user_id=user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    return record


@router.post("/v1/backend/plugins/config", response_model=PluginConfigUpdateResponse)
def update_plugin_config(req: PluginConfigUpdateRequest) -> PluginConfigUpdateResponse:
    try:
        loaded = _manager.update_plugin_config(req.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client_config = req.client_config or req.config
    client_updates = []
    for principal in req.client_principals:
        client_updates.extend(
            _manager.update_client_plugin_config(
                principal,
                client_config,
                remote_plugin_config=req.config,
                timeout_s=req.timeout_s,
            )
        )
    client_updates.extend(
        [
            _push_client_plugin_config(
                url,
                client_config,
                req.timeout_s,
                client_key=_client_key_for_url(url),
            )
            for url in req.client_config_urls
        ]
    )
    return PluginConfigUpdateResponse(
        status="ok",
        loaded_plugins=loaded,
        client_updates=client_updates,
    )


@router.get(
    "/v1/backend/agents/{agent_id}/plugins/config",
    response_model=AgentPluginConfigResponse,
)
def get_agent_plugin_config(agent_id: str) -> AgentPluginConfigResponse:
    sessions = _manager.sessions_for_principal({"agent_id": agent_id})
    plugin_config, config_source = _agent_plugin_config(agent_id, sessions)
    return AgentPluginConfigResponse(
        agent_id=agent_id,
        plugin_config=plugin_config,
        config_source=config_source,
    )


@router.post(
    "/v1/backend/agents/{agent_id}/plugins/config",
    response_model=PluginConfigUpdateResponse,
)
def update_agent_plugin_config(
    agent_id: str,
    req: AgentPluginConfigUpdateRequest,
) -> PluginConfigUpdateResponse:
    client_updates = _manager.update_agent_plugin_config(
        agent_id,
        req.config,
        client_config=req.client_config,
        timeout_s=req.timeout_s,
    )
    return PluginConfigUpdateResponse(
        status="ok",
        loaded_plugins=[],
        client_updates=client_updates,
    )


@router.get(
    "/v1/backend/agents/{agent_id}/plugins/available",
    response_model=AgentPluginAvailableResponse,
)
def get_agent_available_plugins(agent_id: str) -> AgentPluginAvailableResponse:
    remote_options = [
        _plugin_option_dict(name, cls)
        for name, cls in sorted(registered_server_plugins().items())
        if name not in _DEPRECATED_PLUGIN_NAMES
    ]
    local_plugins = _fetch_agent_local_plugins(agent_id)
    return AgentPluginAvailableResponse(
        agent_id=agent_id,
        local_plugins=local_plugins,
        remote_plugins=remote_options,
    )


@router.get("/v1/backend/auditors")
def list_auditors() -> dict[str, list[dict[str, str]]]:
    return {
        "auditors": [
            {"name": name, "description": description}
            for name, description in sorted(auditor_descriptions().items())
        ]
    }


@router.post("/v1/backend/audit/custom/run", response_model=TraceAuditResponse)
def run_custom_trace_audit(req: TraceAuditRequest) -> TraceAuditResponse:
    trace = _manager.get_trace_records(
        req.session_id,
        agent_id=req.agent_id,
        user_id=req.user_id,
    )
    if not trace:
        raise HTTPException(
            status_code=404,
            detail=(
                "trace not found for "
                f"session_id={req.session_id}, agent_id={req.agent_id}, user_id={req.user_id}"
            ),
        )
    try:
        result = _auditors.audit(req.auditor_name, trace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TraceAuditResponse(
        session_id=req.session_id,
        agent_id=req.agent_id,
        user_id=req.user_id,
        auditor_name=req.auditor_name,
        level=result.level,
        reason=result.reason,
        trace_entries=len(trace),
        metadata=result.metadata,
    )


def _push_client_plugin_config(
    url: str,
    config: dict[str, Any],
    timeout_s: float,
    *,
    client_key: str | None = None,
) -> dict[str, Any]:
    body = safe_dumps({"config": config}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if client_key:
        headers["X-AgentGuard-Session-Key"] = client_key
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(timeout_s, 0.1)) as response:
            raw = response.read()
            payload = safe_loads(raw, fallback={})
            return {
                "url": url,
                "status": "ok",
                "status_code": response.status,
                "response": payload,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "url": url,
            "status": "error",
            "status_code": exc.code,
            "error": raw.decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {"url": url, "status": "error", "error": str(exc)}


def _client_key_for_url(url: str) -> str | None:
    for session in _manager.session_pool.list():
        known_urls = {
            session.get("client_config_url"),
            session.get("client_plugin_list_url"),
            session.get("client_health_url"),
        }
        if url in known_urls:
            key = session.get("client_key")
            return str(key) if key else None
    return None


def _agent_plugin_config(
    agent_id: str,
    sessions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    stored = _manager.get_agent_plugin_config(agent_id)
    if stored and isinstance(stored.get("plugin_config"), dict):
        return copy.deepcopy(stored["plugin_config"]), "agent_override"
    for session in sessions:
        merged = merge_plugin_configs(
            session.get("remote_plugin_config") if isinstance(session.get("remote_plugin_config"), dict) else None,
            session.get("client_plugin_config") if isinstance(session.get("client_plugin_config"), dict) else None,
        )
        if isinstance(merged, dict):
            return merged, "agent_override"
    default_config = _default_plugin_config()
    if isinstance(default_config, dict):
        return default_config, "server_default"
    return None, "none"


def _default_plugin_config() -> dict[str, Any] | None:
    source = _manager.plugin_config
    if source is None:
        return None
    if isinstance(source, dict):
        return copy.deepcopy(source)
    try:
        with Path(source).open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    return copy.deepcopy(payload) if isinstance(payload, dict) else None


def _fetch_client_plugin_list(
    url: str,
    *,
    client_key: str | None = None,
    timeout_s: float = 2.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if client_key:
        headers["X-AgentGuard-Session-Key"] = str(client_key)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(timeout_s, 0.1)) as response:
            payload = safe_loads(response.read(), fallback={}) or {}
        plugins = []
        if isinstance(payload, dict):
            plugins = payload.get("plugins") or []
        if not isinstance(plugins, list):
            plugins = []
        return {
            "status": "ok",
            "plugins": [_plugin_payload_dict(item) for item in plugins],
        }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "status": "error",
            "error": raw.decode("utf-8", errors="replace"),
            "plugins": [],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "plugins": []}


def _fetch_agent_local_plugins(agent_id: str) -> list[dict[str, Any]]:
    local_map: dict[str, dict[str, Any]] = {}
    for session in _manager.sessions_for_principal({"agent_id": agent_id}):
        list_url = session.get("client_plugin_list_url")
        if not list_url:
            continue
        result = _fetch_client_plugin_list(
            str(list_url),
            client_key=session.get("client_key"),
        )
        for plugin in result.get("plugins", []):
            name = str(plugin.get("name") or "").strip()
            if name and name not in _DEPRECATED_PLUGIN_NAMES:
                local_map.setdefault(name, plugin)
    return [local_map[name] for name in sorted(local_map)]


def _plugin_option_dict(name: str, cls: type[Any]) -> dict[str, Any]:
    event_types = [
        getattr(event_type, "value", str(event_type))
        for event_type in getattr(cls, "event_types", [])
    ]
    return {
        "name": name,
        "description": str(getattr(cls, "description", "")),
        "event_types": event_types,
        "phases": _plugin_phases(event_types, module_name=getattr(cls, "__module__", "")),
    }


def _plugin_payload_dict(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    event_types = data.get("event_types")
    phases = data.get("phases")
    normalized_event_types = [str(item) for item in event_types] if isinstance(event_types, list) else []
    normalized_phases = [str(item) for item in phases] if isinstance(phases, list) else []
    return {
        "name": str(data.get("name") or ""),
        "description": str(data.get("description") or ""),
        "event_types": normalized_event_types,
        "phases": normalized_phases or _plugin_phases(normalized_event_types),
    }


def _plugin_phases(
    event_types: list[str] | tuple[str, ...],
    *,
    module_name: str = "",
) -> list[str]:
    inferred: list[str] = []
    for event_type in event_types:
        phase = _EVENT_PHASE.get(str(event_type))
        if phase and phase not in inferred:
            inferred.append(phase)
    if inferred:
        return inferred
    module_parts = str(module_name or "").split(".")
    for phase in _KNOWN_PHASES:
        if phase in module_parts and phase not in inferred:
            inferred.append(phase)
    return inferred
