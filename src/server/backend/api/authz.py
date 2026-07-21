"""Reusable authenticated-user and administrator dependencies."""
from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException

from backend.database import DatabaseUnavailable
from backend.user.permissions import is_admin_user
from backend.user.router import SESSION_COOKIE, get_user_store
from backend.user.store import User


def require_current_user(
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    try:
        user = get_user_store().user_for_session(agentguard_user_session)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="login required")
    return user


def require_admin_user(user: User = Depends(require_current_user)) -> User:
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="administrator access required")
    return user


__all__ = ["require_admin_user", "require_current_user"]
