"""User registration, login, and short-lived ticket routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from backend.agents.store import AgentStore
from backend.database import DatabaseUnavailable
from backend.user.email import EmailDeliveryUnavailable, get_email_sender
from backend.user.permissions import is_admin_user
from backend.user.store import (
    DuplicateEmail,
    DuplicateExternalAccount,
    DuplicateUsername,
    EmailVerificationRateLimited,
    ExternalAccountMapping,
    InvalidCredentials,
    InvalidEmailVerification,
    User,
    UserStore,
)

router = APIRouter()

SESSION_COOKIE = "agentguard_user_session"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8)


class RegisterRequest(Credentials):
    email: str = Field(min_length=3, max_length=255)
    verification_code: str = Field(min_length=6, max_length=6)


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class ExternalAccountBindRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    metadata_json: str | None = None


class DifyBindRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    metadata_json: str | None = None


def get_user_store() -> UserStore:
    return UserStore()


@router.post("/v1/user/register")
def register_user(req: RegisterRequest) -> dict[str, Any]:
    store = _store_or_503()
    try:
        user = store.create_user(
            req.username,
            req.password,
            email=req.email,
            verification_code=req.verification_code,
        )
    except DuplicateUsername as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DuplicateEmail as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidEmailVerification as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": _user_payload(user)}


@router.post("/v1/user/register/email-code")
def send_register_email_code(req: EmailCodeRequest) -> dict[str, Any]:
    store = _store_or_503()
    try:
        if store.user_for_email(req.email) is not None:
            raise DuplicateEmail(f"email already exists: {req.email.strip().lower()}")
        sender = get_email_sender()
        issue = store.issue_email_verification_code(req.email)
        sender.send_verification_code(
            to_email=issue.email,
            code=issue.code,
        )
    except DuplicateEmail as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmailVerificationRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except EmailDeliveryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "email": issue.email,
        "expires_at": _iso(issue.expires_at),
        "cooldown_seconds": issue.cooldown_seconds,
    }


@router.post("/v1/user/login")
def login_user(req: Credentials, response: Response) -> dict[str, Any]:
    store = _store_or_503()
    try:
        user = store.authenticate(req.username, req.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    session = store.create_web_session(user)
    _set_session_cookie(response, session.token, session.expires_at)
    return {"user": _user_payload(user), "expires_at": _iso(session.expires_at)}


@router.post("/v1/user/logout")
def logout_user(
    response: Response,
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, str]:
    store = _store_or_503()
    store.revoke_session(agentguard_user_session)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
    return {"status": "ok"}


@router.get("/v1/user/me")
def current_user(
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    return {"user": _user_payload(user)}


@router.post("/v1/user/password")
def change_password(
    req: PasswordChangeRequest,
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, str]:
    user = _current_user_or_401(agentguard_user_session)
    try:
        _store_or_503().change_password(
            user,
            current_password=req.current_password,
            new_password=req.new_password,
        )
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@router.post("/v1/user/tickets")
def create_ticket(
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    ticket = _store_or_503().create_ticket(user)
    ttl_seconds = max(
        0,
        int((ticket.expires_at - datetime.now(timezone.utc)).total_seconds()),
    )
    return {
        "ticket": ticket.ticket,
        "ticket_prefix": ticket.prefix,
        "expires_at": _iso(ticket.expires_at),
        "ttl_seconds": ttl_seconds,
    }


@router.get("/v1/user/tickets")
def list_tickets(
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    tickets = _store_or_503().list_tickets(user)
    return {"tickets": [_ticket_payload(item) for item in tickets]}


@router.post("/v1/user/dify/bind")
def bind_dify_account(
    req: DifyBindRequest,
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    return _bind_external_account(
        provider="dify",
        email=req.email,
        display_name=req.display_name,
        metadata_json=req.metadata_json,
        agentguard_user_session=agentguard_user_session,
    )


@router.post("/v1/user/external-accounts")
def bind_external_account(
    req: ExternalAccountBindRequest,
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    return _bind_external_account(
        provider=req.provider,
        email=req.email,
        display_name=req.display_name,
        metadata_json=req.metadata_json,
        agentguard_user_session=agentguard_user_session,
    )


def _bind_external_account(
    *,
    provider: str,
    email: str,
    display_name: str | None,
    metadata_json: str | None,
    agentguard_user_session: str | None,
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    try:
        mapping = _store_or_503().bind_external_account(
            user,
            provider=provider,
            account_email=email,
            display_name=display_name,
            metadata_json=metadata_json,
        )
    except DuplicateExternalAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        bindings = AgentStore().bind_existing_agents_for_external_account(
            user_id=user.id,
            provider=mapping.provider,
            account_email=mapping.account_email,
        )
    except DatabaseUnavailable:
        bindings = None
    return {
        "external_account": _external_account_payload(mapping),
        "user_agent_bindings": bindings,
    }


@router.get("/v1/user/external-accounts")
def list_external_accounts(
    provider: str | None = None,
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    accounts = _store_or_503().list_external_accounts(user, provider=provider)
    return {"external_accounts": [_external_account_payload(item) for item in accounts]}


@router.get("/v1/user/openclaw-bindings")
def list_openclaw_bindings(
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    try:
        bindings = AgentStore().list_user_agent_bindings(user.id, provider="openclaw")
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not bindings:
        return {"openclaw_bindings": []}
    return {
        "openclaw_bindings": [
            {
                "provider": "openclaw",
                "account_email": "-",
                "display_name": "-",
                "agent_count": len(bindings),
            }
        ]
    }


@router.delete("/v1/user/external-accounts/{mapping_id}")
def delete_external_account(
    mapping_id: int,
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    mappings = _store_or_503().list_external_accounts(user)
    mapping = next((item for item in mappings if item.id == int(mapping_id)), None)
    deleted = _store_or_503().delete_external_account(user, mapping_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="external account not found")
    if mapping is not None:
        try:
            AgentStore().unbind_agents_for_external_account(
                user_id=user.id,
                provider=mapping.provider,
                account_email=mapping.account_email,
            )
        except DatabaseUnavailable:
            pass
    return {"status": "ok", "external_account_id": mapping_id}


@router.delete("/v1/user/openclaw-bindings")
def delete_openclaw_binding(
    agentguard_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _current_user_or_401(agentguard_user_session)
    try:
        result = AgentStore().unbind_user_provider(
            user_id=user.id,
            provider="openclaw",
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not result["unbound_count"]:
        raise HTTPException(status_code=404, detail="openclaw binding not found")
    return {"status": "ok", **result}


def _store_or_503() -> UserStore:
    try:
        return get_user_store()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _current_user_or_401(token: str | None) -> User:
    store = _store_or_503()
    user = store.user_for_session(token)
    if user is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return user


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=max_age,
        path="/",
    )


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "is_admin": is_admin_user(user),
    }


def _ticket_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "ticket_prefix": item.get("ticket_prefix"),
        "expires_at": _iso(item.get("expires_at")),
        "created_at": _iso(item.get("created_at")),
        "last_used_at": _iso(item.get("last_used_at")),
        "expired": bool(item.get("expired")),
    }


def _external_account_payload(item: ExternalAccountMapping) -> dict[str, Any]:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "provider": item.provider,
        "account_email": item.account_email,
        "display_name": item.display_name,
        "metadata_json": item.metadata_json,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(value)
