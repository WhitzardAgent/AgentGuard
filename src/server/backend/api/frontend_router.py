"""Frontend/admin API routes for checker config and session management."""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    CheckerConfigUpdateRequest,
    CheckerConfigUpdateResponse,
    TraceAuditRequest,
    TraceAuditResponse,
)
from backend.app_state import get_console, get_manager
from backend.audit import auditor_descriptions, auditor_manager
from shared.utils.json import safe_dumps, safe_loads

router = APIRouter()

# Bind console observers to the shared manager during API startup.
_manager = get_manager()
get_console()
_auditors = auditor_manager()


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
            session_id=req.session_id,
            agent_id=req.agent_id,
            user_id=req.user_id,
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
