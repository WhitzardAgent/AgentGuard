"""Client-facing API routes: guard decide, policy snapshot, trace, skills."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.api.schemas import (
    GuardDecideRequest,
    GuardDecideResponse,
    SessionRegisterRequest,
    SkillRunRequest,
    ToolReportRequest,
    TraceUploadRequest,
)
from backend.app_state import get_console, get_manager, get_skills
from shared.schemas.context import RuntimeContext
from backend.runtime.policy.snapshot_builder import snapshot_dict

router = APIRouter()

_manager = get_manager()
_console = get_console()
_skills = get_skills()


@router.post("/v1/server/guard/decide", response_model=GuardDecideResponse)
def guard_decide(req: GuardDecideRequest, request: Request) -> GuardDecideResponse:
    body = req.model_dump()
    body["_transport"] = _transport_metadata(request, enforce_session_key=True)
    try:
        result = _manager.decide(body)
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return GuardDecideResponse(**result)


@router.get("/v1/server/approvals/{ticket_id}")
def approval_status(ticket_id: str, request: Request, wait_ms: int = 0) -> dict[str, Any]:
    _validate_client_session(request)
    ticket = _manager.review_queue.get(ticket_id)
    if ticket is None or not _ticket_belongs_to_request(ticket, request):
        raise HTTPException(status_code=404, detail="ticket not found")
    waited = _manager.review_queue.wait(ticket_id, timeout_s=max(wait_ms, 0) / 1000.0)
    if waited is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return waited


@router.get("/v1/server/policy/snapshot")
def policy_snapshot(request: Request) -> dict:
    _validate_client_session(request)
    return snapshot_dict(_manager.policy.store)


@router.post("/v1/server/trace/upload")
def trace_upload(req: TraceUploadRequest, request: Request) -> dict:
    trace = req.model_dump()
    trace["_transport"] = _transport_metadata(request, enforce_session_key=True)
    try:
        count = _manager.record_uploaded_trace(trace)
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return {"status": "received", "entries": count}


@router.post("/v1/server/tools/report")
def report_tool(req: ToolReportRequest, request: Request) -> dict[str, Any]:
    _validate_client_session(request)
    tool = _console.register_tool(req.context, req.tool)
    if tool is None:
        raise HTTPException(status_code=400, detail="agent_id and tool.name are required")
    return {"status": "ok", "tool": tool}


@router.post("/v1/server/session/register")
def register_session(req: SessionRegisterRequest, request: Request) -> dict[str, Any]:
    context = RuntimeContext.from_dict(req.context)
    try:
        record = _manager.register_client_session(
            context,
            client_ip=_client_ip(request),
            client_key=request.headers.get("x-agentguard-session-key"),
            enforce_key=True,
        )
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return {"status": "ok", "session": record}


@router.post("/v1/server/skills/run")
def skills_run(req: SkillRunRequest, request: Request) -> dict:
    _validate_client_session(request)
    return _skills.run(req.model_dump())


@router.post("/v1/server/session/unregister")
def unregister_session(request: Request) -> dict[str, Any]:
    session_id = request.headers.get("x-agentguard-session-id")
    if not session_id:
        raise _session_key_error(PermissionError("missing client session id"))
    agent_id = request.headers.get("x-agentguard-agent-id")
    user_id = request.headers.get("x-agentguard-user-id")
    try:
        removed = _manager.session_pool.remove(
            session_id,
            agent_id=agent_id,
            user_id=user_id,
            client_key=request.headers.get("x-agentguard-session-key"),
            enforce_key=True,
        )
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return {"status": "ok", "session_id": session_id, "removed": removed}


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _transport_metadata(request: Request, *, enforce_session_key: bool) -> dict[str, Any]:
    return {
        "client_ip": _client_ip(request),
        "client_key": request.headers.get("x-agentguard-session-key"),
        "agent_id": request.headers.get("x-agentguard-agent-id"),
        "user_id": request.headers.get("x-agentguard-user-id"),
        "enforce_session_key": enforce_session_key,
    }


def _validate_client_session(request: Request) -> None:
    session_id = request.headers.get("x-agentguard-session-id")
    if not session_id:
        raise _session_key_error(PermissionError("missing client session id"))
    try:
        record = _manager.session_pool.touch(
            session_id,
            agent_id=request.headers.get("x-agentguard-agent-id"),
            user_id=request.headers.get("x-agentguard-user-id"),
            client_ip=_client_ip(request),
            client_key=request.headers.get("x-agentguard-session-key"),
            enforce_key=True,
        )
        if record is None:
            raise PermissionError("unknown client session")
    except PermissionError as exc:
        raise _session_key_error(exc) from exc


def _session_key_error(exc: PermissionError) -> HTTPException:
    message = str(exc)
    status = 401 if "missing" in message else 403
    return HTTPException(status_code=status, detail=message)


def _ticket_belongs_to_request(ticket: dict[str, Any], request: Request) -> bool:
    principal = dict(ticket.get("principal") or {})
    return (
        str(principal.get("session_id") or "")
        == str(request.headers.get("x-agentguard-session-id") or "")
        and str(principal.get("agent_id") or "")
        == str(request.headers.get("x-agentguard-agent-id") or "")
        and str(principal.get("user_id") or "")
        == str(request.headers.get("x-agentguard-user-id") or "")
    )
