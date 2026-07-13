"""Client-facing API routes: guard decide, policy snapshot, trace, skills."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.api.schemas import (
    AgentCatalogSyncRequest,
    AgentRegisterRequest,
    GuardDecideRequest,
    GuardDecideResponse,
    McpReportRequest,
    RuntimeSessionCreateRequest,
    SessionRegisterRequest,
    SkillReportRequest,
    SkillRunRequest,
    ToolReportRequest,
    ToolSyncRequest,
    TraceUploadRequest,
)
from backend.app_state import get_console, get_manager, get_skills
from backend.api.auth import configured_backend_api_key
from backend.agents.store import AgentStore
from backend.auth.broker import AuthBrokerError, get_dify_auth_broker
from backend.auth.dependencies import (
    apply_auth_context_to_context,
    authenticate_dpop_request,
    dpop_access_token,
)
from backend.auth.models import AuthContext
from backend.database import DatabaseUnavailable
from shared.schemas.context import RuntimeContext
from backend.runtime.policy.snapshot_builder import snapshot_dict

router = APIRouter()

_manager = get_manager()
_console = get_console()
_skills = get_skills()


@router.post("/v1/server/guard/decide", response_model=GuardDecideResponse)
def guard_decide(req: GuardDecideRequest, request: Request) -> GuardDecideResponse:
    auth = _authenticate_runtime(request)
    body = req.model_dump()
    body["context"] = apply_auth_context_to_context(body.get("context") or {}, auth)
    body["_transport"] = _transport_metadata(request, enforce_session_key=auth is None, auth=auth)
    try:
        result = _manager.decide(body)
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return GuardDecideResponse(**result)


@router.get("/v1/server/approvals/{ticket_id}")
def approval_status(ticket_id: str, request: Request, wait_ms: int = 0) -> dict[str, Any]:
    auth = _authenticate_runtime(request)
    ticket = _manager.review_queue.get(ticket_id)
    if ticket is None or not _ticket_belongs_to_request(ticket, request, auth=auth):
        raise HTTPException(status_code=404, detail="ticket not found")
    waited = _manager.review_queue.wait(ticket_id, timeout_s=max(wait_ms, 0) / 1000.0)
    if waited is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return waited


@router.get("/v1/server/policy/snapshot")
def policy_snapshot(request: Request) -> dict:
    _authenticate_runtime(request)
    return snapshot_dict(_manager.policy.store)


@router.post("/v1/server/trace/upload")
def trace_upload(req: TraceUploadRequest, request: Request) -> dict:
    auth = _authenticate_runtime(request)
    trace = req.model_dump()
    if auth is not None:
        trace.update(
            {
                "session_id": auth.session_id,
                "agent_id": auth.agent_id,
                "user_id": auth.user_id,
            }
        )
    trace["_transport"] = _transport_metadata(request, enforce_session_key=auth is None, auth=auth)
    try:
        count = _manager.record_uploaded_trace(trace)
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return {"status": "received", "entries": count}


@router.post("/v1/server/tools/report")
def report_tool(req: ToolReportRequest, request: Request) -> dict[str, Any]:
    auth = _authenticate_runtime(request)
    context = apply_auth_context_to_context(req.context, auth)
    tool = _console.register_tool(context, req.tool)
    if tool is None:
        raise HTTPException(status_code=400, detail="agent_id and tool.name are required")
    return {"status": "ok", "tool": tool}


@router.post("/v1/server/tools/sync")
def sync_tools(req: ToolSyncRequest, request: Request) -> dict[str, Any]:
    auth = _authenticate_runtime(request)
    context = apply_auth_context_to_context(req.context, auth)
    result = _console.sync_tools(context, req.tools)
    if result is None:
        raise HTTPException(status_code=400, detail="agent_id is required")
    return {"status": "ok", **result}


@router.post("/v1/server/agents/register")
def register_agent(req: AgentRegisterRequest, request: Request) -> dict[str, Any]:
    _validate_adapter_api_key(request)
    try:
        result = AgentStore().register_agent(
            provider=req.provider,
            provider_instance_id=req.provider_instance_id,
            tenant_id=req.tenant_id,
            external_agent_id=req.external_agent_id,
            agent_type=req.agent_type,
            name=req.name,
            description=req.description,
            account_email=req.account_email,
            public_key_jwk=req.public_key_jwk,
            metadata=req.metadata,
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agent = result.agent
    credential = result.credential
    return {
        "status": "ok",
        "agent": {
            "agent_id": agent.agent_id,
            "agent_identity_code": agent.agent_identity_code,
            "provider": agent.provider,
            "provider_instance_id": agent.provider_instance_id,
            "tenant_id": agent.tenant_id,
            "external_agent_id": agent.external_agent_id,
            "agent_type": agent.agent_type,
            "public_key_thumbprint": agent.public_key_thumbprint,
            "status": agent.status,
        },
        "credential": {
            "credential_id": credential.credential_id,
            "agent_id": credential.agent_id,
            "public_key_thumbprint": credential.public_key_thumbprint,
            "issuer": credential.issuer,
            "status": credential.status,
            "valid_from": credential.valid_from.isoformat() if credential.valid_from else None,
            "valid_to": credential.valid_to.isoformat() if credential.valid_to else None,
        },
        "user_agent": {
            "user_id": result.user_id,
            "account_email": result.account_email,
            "bound": result.user_id is not None,
            "created": result.user_binding_created,
            "updated": result.user_binding_updated,
        },
    }


@router.post("/v1/server/agents/sync")
def sync_agents(req: AgentCatalogSyncRequest, request: Request) -> dict[str, Any]:
    _validate_adapter_api_key(request)
    try:
        result = AgentStore().sync_provider_agents(
            provider=req.provider,
            provider_instance_id=req.provider_instance_id,
            tenant_id=req.tenant_id,
            agent_type=req.agent_type,
            external_agent_ids=req.external_agent_ids,
            metadata=req.metadata,
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", **result}


@router.post("/v1/server/skills/report")
def report_skills(req: SkillReportRequest, request: Request) -> dict[str, Any]:
    auth = _authenticate_runtime(request)
    context = apply_auth_context_to_context(req.context, auth)
    result = _console.register_skills(context, req.skills, req.scan)
    if result is None:
        raise HTTPException(status_code=400, detail="agent_id is required")
    return {
        "status": "ok",
        "skill_count": result["skill_count"],
        "skills": result["skills"],
    }


@router.post("/v1/server/mcps/report")
def report_mcps(req: McpReportRequest, request: Request) -> dict[str, Any]:
    auth = _authenticate_runtime(request)
    context = apply_auth_context_to_context(req.context, auth)
    result = _console.register_mcps(context, req.mcps, req.scan)
    if result is None:
        raise HTTPException(status_code=400, detail="agent_id is required")
    return {
        "status": "ok",
        "mcp_count": result["mcp_count"],
        "mcps": result["mcps"],
    }


@router.post("/v1/server/session/register")
def register_session(req: SessionRegisterRequest, request: Request) -> dict[str, Any]:
    context = RuntimeContext.from_dict(req.context)
    try:
        record = _manager.register_client_session(
            context,
            client_ip=_client_ip(request),
            client_key=request.headers.get("x-agentguard-session-key"),
            user_ticket=request.headers.get("x-agentguard-user-ticket"),
            enforce_key=True,
        )
    except PermissionError as exc:
        raise _session_key_error(exc) from exc
    return {"status": "ok", "session": record}


@router.post("/v1/server/session/create")
def create_runtime_session(req: RuntimeSessionCreateRequest, request: Request) -> dict[str, Any]:
    try:
        provider = str(req.provider or "").strip().lower()
        broker = get_dify_auth_broker()
        if provider == "langchain":
            issue = broker.create_langchain_ticket_session(
                user_ticket=req.user_ticket or request.headers.get("x-agentguard-user-ticket"),
                metadata=req.metadata,
                dpop_proof=request.headers.get("dpop"),
                request_body=req.model_dump(exclude_none=True),
                method=request.method,
                url=str(request.url),
            )
        else:
            _validate_adapter_api_key(request)
            if not req.agent_id:
                raise HTTPException(status_code=400, detail="agent_id is required")
            if not req.account_email:
                raise HTTPException(status_code=400, detail="account_email is required")
            issue = broker.create_session(
                provider=req.provider,
                external_session_id=req.external_session_id,
                agent_id=req.agent_id,
                account_email=req.account_email,
                external_user_id=req.external_user_id,
                metadata=req.metadata,
                dpop_proof=request.headers.get("dpop"),
                agent_proof=request.headers.get("x-agentguard-agent-proof"),
                request_body=req.model_dump(exclude_none=True),
                method=request.method,
                url=str(request.url),
            )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthBrokerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    _register_auth_context(_auth_context_from_issue(issue), request)
    return _runtime_session_issue_payload(issue)


@router.post("/v1/server/session/refresh")
def refresh_runtime_session(request: Request) -> dict[str, Any]:
    _reject_legacy_identity_headers(request)
    token = dpop_access_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="missing DPoP access token")
    try:
        issue = get_dify_auth_broker().refresh_session(
            token=token,
            dpop_proof=request.headers.get("dpop"),
            method=request.method,
            url=str(request.url),
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthBrokerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    _register_auth_context(_auth_context_from_issue(issue), request)
    return _runtime_session_issue_payload(issue)


@router.post("/v1/server/session/close")
def close_runtime_session(request: Request) -> dict[str, Any]:
    _reject_legacy_identity_headers(request)
    token = dpop_access_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="missing DPoP access token")
    try:
        auth = get_dify_auth_broker().close_session(
            token=token,
            dpop_proof=request.headers.get("dpop"),
            method=request.method,
            url=str(request.url),
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthBrokerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"status": "ok", "session_id": auth.session_id, "closed": True}


@router.post("/v1/server/skills/run")
def skills_run(req: SkillRunRequest, request: Request) -> dict:
    _authenticate_runtime(request)
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


def _transport_metadata(
    request: Request,
    *,
    enforce_session_key: bool,
    auth: AuthContext | None = None,
) -> dict[str, Any]:
    return {
        "client_ip": _client_ip(request),
        "client_key": None if auth is not None else request.headers.get("x-agentguard-session-key"),
        "user_ticket": request.headers.get("x-agentguard-user-ticket"),
        "agent_id": auth.agent_id if auth is not None else request.headers.get("x-agentguard-agent-id"),
        "user_id": auth.user_id if auth is not None else request.headers.get("x-agentguard-user-id"),
        "enforce_session_key": enforce_session_key,
    }


def _authenticate_runtime(request: Request) -> AuthContext | None:
    auth = authenticate_dpop_request(request)
    if auth is not None:
        _register_auth_context(auth, request)
        return auth
    _validate_client_session(request)
    return None


def _register_auth_context(auth: AuthContext, request: Request) -> None:
    context = RuntimeContext(
        session_id=auth.session_id,
        agent_id=auth.agent_id,
        user_id=auth.user_id,
        metadata=auth.to_metadata(),
    )
    _manager.register_client_session(
        context,
        client_ip=_client_ip(request),
        enforce_key=False,
        push_config=False,
    )


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
            metadata=_identity_metadata_from_request(request),
        )
        if record is None:
            raise PermissionError("unknown client session")
    except PermissionError as exc:
        raise _session_key_error(exc) from exc


def _validate_adapter_api_key(request: Request) -> None:
    expected = configured_backend_api_key()
    if not expected:
        return
    bearer = request.headers.get("authorization") or ""
    scheme, _, token = bearer.partition(" ")
    provided = request.headers.get("x-api-key") or (token.strip() if scheme.lower() == "bearer" else "")
    if not provided:
        raise HTTPException(status_code=401, detail="missing adapter API key")
    if provided != expected:
        raise HTTPException(status_code=403, detail="invalid adapter API key")


def _reject_legacy_identity_headers(request: Request) -> None:
    legacy = [
        "x-agentguard-session-id",
        "x-agentguard-agent-id",
        "x-agentguard-user-id",
        "x-agentguard-session-key",
    ]
    present = [name for name in legacy if request.headers.get(name)]
    if present:
        raise HTTPException(
            status_code=400,
            detail=f"legacy identity headers are not allowed with DPoP: {', '.join(present)}",
        )


def _runtime_session_issue_payload(issue: Any) -> dict[str, Any]:
    session = issue.session
    return {
        "status": "ok",
        "agent_id": session.agent_id,
        "session_id": session.session_id,
        "user_id": str(session.user_id),
        "session_token": issue.session_token,
        "issued_at": issue.issued_at,
        "expires_at": issue.expires_at,
        "auth_method": (
            "dify_api_key_dpop" if session.provider == "dify" else f"{session.provider}_dpop"
        ),
        "external_session_id": session.external_session_id,
    }


def _auth_context_from_issue(issue: Any) -> AuthContext:
    session = issue.session
    return AuthContext(
        session_id=session.session_id,
        agent_id=session.agent_id,
        user_id=str(session.user_id),
        token_jti=issue.token_jti,
        dpop_jkt=session.dpop_jkt,
        external_provider=session.provider,
        external_session_id=session.external_session_id,
        scope=["runtime"],
    )


def _session_key_error(exc: PermissionError) -> HTTPException:
    message = str(exc)
    if "user ticket validation is unavailable" in message:
        return HTTPException(status_code=503, detail=message)
    status = 401 if (
        "missing" in message
        or "user ticket" in message
    ) else 403
    return HTTPException(status_code=status, detail=message)


def _identity_metadata_from_request(request: Request) -> dict[str, Any]:
    ticket = request.headers.get("x-agentguard-user-ticket")
    if not ticket:
        return {}
    from backend.user.store import resolve_user_ticket  # noqa: PLC0415

    identity = resolve_user_ticket(ticket)
    if identity is None:
        return {}
    return {
        "canonical_user_id": identity.user_id,
        "canonical_username": identity.username,
        "user_ticket_id": identity.ticket_id,
        "user_ticket_prefix": identity.ticket_prefix,
    }


def _ticket_belongs_to_request(
    ticket: dict[str, Any],
    request: Request,
    *,
    auth: AuthContext | None = None,
) -> bool:
    principal = dict(ticket.get("principal") or {})
    if auth is not None:
        return (
            str(principal.get("session_id") or "") == auth.session_id
            and str(principal.get("agent_id") or "") == auth.agent_id
            and str(principal.get("user_id") or "") == auth.user_id
        )
    return (
        str(principal.get("session_id") or "")
        == str(request.headers.get("x-agentguard-session-id") or "")
        and str(principal.get("agent_id") or "")
        == str(request.headers.get("x-agentguard-agent-id") or "")
        and str(principal.get("user_id") or "")
        == str(request.headers.get("x-agentguard-user-id") or "")
    )
