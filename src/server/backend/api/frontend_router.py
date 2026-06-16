"""Frontend/admin API routes for checker config and session management."""
from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    AgentCheckerAvailableResponse,
    AgentCheckerConfigResponse,
    AgentCheckerConfigUpdateRequest,
    CheckerConfigUpdateRequest,
    CheckerConfigUpdateResponse,
    TraceAuditRequest,
    TraceAuditResponse,
)
from backend.app_state import get_console, get_manager
from backend.audit import auditor_descriptions, auditor_manager
from backend.runtime.checkers.registry import registered_checkers as registered_server_checkers
from shared.schemas.events import EventType
from shared.utils.json import safe_dumps, safe_loads

router = APIRouter()

# Bind console observers to the shared manager during API startup.
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


@router.post("/v1/backend/checkers/config", response_model=CheckerConfigUpdateResponse)
def update_checker_config(req: CheckerConfigUpdateRequest) -> CheckerConfigUpdateResponse:
    try:
        loaded = _manager.update_checker_config(req.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client_config = req.client_config or req.config
    client_updates = []
    for principal in req.client_principals:
        client_updates.extend(
            _manager.update_client_checker_config(
                principal,
                client_config,
                remote_checker_config=req.config,
                timeout_s=req.timeout_s,
            )
        )
    client_updates.extend(
        [
            _push_client_checker_config(
                url,
                client_config,
                req.timeout_s,
                client_key=_client_key_for_url(url),
            )
            for url in req.client_config_urls
        ]
    )
    return CheckerConfigUpdateResponse(
        status="ok",
        loaded_checkers=loaded,
        client_updates=client_updates,
    )


@router.get(
    "/v1/backend/agents/{agent_id}/checkers/config",
    response_model=AgentCheckerConfigResponse,
)
def get_agent_checker_config(agent_id: str) -> AgentCheckerConfigResponse:
    sessions = _manager.sessions_for_principal({"agent_id": agent_id})
    checker_config, config_source = _agent_checker_config(sessions)
    return AgentCheckerConfigResponse(
        agent_id=agent_id,
        checker_config=checker_config,
        config_source=config_source,
    )


@router.post(
    "/v1/backend/agents/{agent_id}/checkers/config",
    response_model=CheckerConfigUpdateResponse,
)
def update_agent_checker_config(
    agent_id: str,
    req: AgentCheckerConfigUpdateRequest,
) -> CheckerConfigUpdateResponse:
    client_updates = _manager.update_agent_checker_config(
        agent_id,
        req.config,
        client_config=req.client_config,
        timeout_s=req.timeout_s,
    )
    return CheckerConfigUpdateResponse(
        status="ok",
        loaded_checkers=[],
        client_updates=client_updates,
    )


@router.get(
    "/v1/backend/agents/{agent_id}/checkers/available",
    response_model=AgentCheckerAvailableResponse,
)
def get_agent_available_checkers(agent_id: str) -> AgentCheckerAvailableResponse:
    remote_options = [
        _checker_option_dict(name, cls)
        for name, cls in sorted(registered_server_checkers().items())
    ]
    return AgentCheckerAvailableResponse(
        agent_id=agent_id,
        local_checkers=_fetch_agent_local_checkers(agent_id),
        remote_checkers=remote_options,
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
        result = _auditors.audit(
            req.auditor_name,
            trace,
        )
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


def _push_client_checker_config(
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
            session.get("client_checker_list_url"),
            session.get("client_health_url"),
        }
        if url in known_urls:
            key = session.get("client_key")
            return str(key) if key else None
    return None


def _agent_checker_config(
    sessions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    for session in sessions:
        config = session.get("remote_checker_config")
        if isinstance(config, dict):
            return copy.deepcopy(config), "agent_override"
    for session in sessions:
        config = session.get("client_checker_config")
        if isinstance(config, dict):
            return copy.deepcopy(config), "agent_override"
    default_config = _default_checker_config()
    if isinstance(default_config, dict):
        return default_config, "server_default"
    return None, "none"


def _default_checker_config() -> dict[str, Any] | None:
    source = _manager.checker_config
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


def _fetch_client_checker_list(
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
        checkers = payload.get("checkers") if isinstance(payload, dict) else []
        if not isinstance(checkers, list):
            checkers = []
        return {
            "status": "ok",
            "checkers": [_checker_payload_dict(item) for item in checkers],
        }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "status": "error",
            "error": raw.decode("utf-8", errors="replace"),
            "checkers": [],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "checkers": []}


def _fetch_agent_local_checkers(agent_id: str) -> list[dict[str, Any]]:
    local_map: dict[str, dict[str, Any]] = {}
    for session in _manager.sessions_for_principal({"agent_id": agent_id}):
        list_url = session.get("client_checker_list_url")
        if not list_url:
            continue
        result = _fetch_client_checker_list(
            str(list_url),
            client_key=session.get("client_key"),
        )
        for checker in result.get("checkers", []):
            name = str(checker.get("name") or "").strip()
            if name:
                local_map.setdefault(name, checker)
    return [local_map[name] for name in sorted(local_map)]


def _checker_option_dict(name: str, cls: type[Any]) -> dict[str, Any]:
    event_types = [
        getattr(event_type, "value", str(event_type))
        for event_type in getattr(cls, "event_types", [])
    ]
    return {
        "name": name,
        "description": str(getattr(cls, "description", "")),
        "event_types": event_types,
        "phases": _checker_phases(event_types, module_name=getattr(cls, "__module__", "")),
    }


def _checker_payload_dict(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    event_types = data.get("event_types")
    phases = data.get("phases")
    normalized_event_types = [str(item) for item in event_types] if isinstance(event_types, list) else []
    normalized_phases = [str(item) for item in phases] if isinstance(phases, list) else []
    return {
        "name": str(data.get("name") or ""),
        "description": str(data.get("description") or ""),
        "event_types": normalized_event_types,
        "phases": normalized_phases or _checker_phases(normalized_event_types),
    }


def _checker_phases(
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
