"""User registration, login, and short-lived ticket routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from backend.database import DatabaseUnavailable
from backend.user.store import (
    DuplicateUsername,
    InvalidCredentials,
    User,
    UserStore,
)

router = APIRouter()

SESSION_COOKIE = "agentguard_user_session"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8)


def get_user_store() -> UserStore:
    return UserStore()


@router.post("/v1/user/register")
def register_user(req: Credentials) -> dict[str, Any]:
    store = _store_or_503()
    try:
        user = store.create_user(req.username, req.password)
    except DuplicateUsername as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": _user_payload(user)}


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
    return {"id": user.id, "username": user.username}


def _ticket_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "ticket_prefix": item.get("ticket_prefix"),
        "expires_at": _iso(item.get("expires_at")),
        "created_at": _iso(item.get("created_at")),
        "last_used_at": _iso(item.get("last_used_at")),
        "expired": bool(item.get("expired")),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(value)
