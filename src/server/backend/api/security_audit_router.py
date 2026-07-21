"""Administrator-only agent-wide security audit API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.authz import require_admin_user
from backend.api.security_audit_schemas import SecurityAuditCreateRequest
from backend.app_state import get_agent_audit_service
from backend.audit.agent_manager import AgentAuditorManager
from backend.audit.agent_service import AgentAuditConflict, AgentAuditNotFound
from backend.audit.agent_store import AgentAuditStore
from backend.database import DatabaseUnavailable
from backend.user.store import User

router = APIRouter(prefix="/v1/backend/security-audits", tags=["security-audits"])


@router.get("/auditors")
def list_agent_auditors(_: User = Depends(require_admin_user)) -> dict[str, Any]:
    return {"auditors": AgentAuditorManager.descriptions()}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_security_audit(
    body: SecurityAuditCreateRequest,
    admin: User = Depends(require_admin_user),
) -> dict[str, Any]:
    try:
        return get_agent_audit_service().create_run(
            agent_id=body.agent_id,
            requested_by_user_id=admin.id,
            auditor_name=body.auditor_name,
            start_at=body.start_at,
            end_at=body.end_at,
            llm_config=(
                body.llm_config.model_dump(exclude_none=True)
                if body.llm_config is not None
                else None
            ),
        )
    except AgentAuditNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentAuditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@router.get("")
def list_security_audits(
    agent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_admin_user),
) -> dict[str, Any]:
    try:
        return {"runs": AgentAuditStore().list_runs(agent_id=agent_id, limit=limit)}
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@router.get("/{run_id}")
def get_security_audit(run_id: str, _: User = Depends(require_admin_user)) -> dict[str, Any]:
    try:
        run = AgentAuditStore().get_run(run_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if run is None:
        raise HTTPException(status_code=404, detail="security audit not found")
    return run


@router.get("/{run_id}/findings")
def list_security_audit_findings(run_id: str, _: User = Depends(require_admin_user)) -> dict[str, Any]:
    try:
        store = AgentAuditStore()
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="security audit not found")
        return {"findings": store.list_findings(run_id)}
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@router.get("/{run_id}/sessions")
def list_security_audit_sessions(run_id: str, _: User = Depends(require_admin_user)) -> dict[str, Any]:
    try:
        store = AgentAuditStore()
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="security audit not found")
        return {"sessions": store.list_session_results(run_id)}
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


__all__ = ["router"]
