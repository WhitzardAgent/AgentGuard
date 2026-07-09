"""FastAPI helpers for runtime DPoP authentication."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from backend.auth.broker import AuthBrokerError, get_dify_auth_broker
from backend.auth.models import AuthContext
from backend.database import DatabaseUnavailable


def dpop_access_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "dpop" or not token.strip():
        return None
    return token.strip()


def is_dpop_request(request: Request) -> bool:
    return bool(dpop_access_token(request) or request.headers.get("dpop"))


def authenticate_dpop_request(request: Request) -> AuthContext | None:
    token = dpop_access_token(request)
    if token is None:
        if request.headers.get("dpop"):
            raise HTTPException(status_code=401, detail="missing DPoP access token")
        return None
    _reject_legacy_identity_headers(request)
    try:
        auth = get_dify_auth_broker().authenticate_runtime_request(
            token=token,
            dpop_proof=request.headers.get("dpop"),
            method=request.method,
            url=str(request.url),
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthBrokerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    request.state.auth_context = auth
    return auth


def apply_auth_context_to_context(
    context: dict[str, Any],
    auth: AuthContext | None,
) -> dict[str, Any]:
    if auth is None:
        return dict(context or {})
    updated = dict(context or {})
    metadata = dict(updated.get("metadata") or {})
    metadata.pop("client_session_key", None)
    metadata.update(auth.to_metadata())
    updated.update(
        {
            "session_id": auth.session_id,
            "agent_id": auth.agent_id,
            "user_id": auth.user_id,
            "metadata": metadata,
        }
    )
    return updated


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
