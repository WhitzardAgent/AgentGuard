"""Session context propagation via `contextvars`."""

from __future__ import annotations

import contextlib
import contextvars
import uuid
from typing import Iterator

from agentguard.models.events import Principal
from agentguard.models.sessions import GuardSession


_current: contextvars.ContextVar[GuardSession | None] = contextvars.ContextVar(
    "agentguard_session", default=None
)


def current_session() -> GuardSession | None:
    return _current.get()


def current_principal() -> Principal | None:
    s = _current.get()
    return s.principal if s else None


def set_principal(principal: Principal) -> GuardSession:
    session = _current.get()
    if session is None:
        session = GuardSession(session_id=principal.session_id, principal=principal)
    else:
        session.principal = principal
    _current.set(session)
    return session


# ── imperative start / end (no context manager required) ─────────────────────

def push_session(
    *,
    session_id: str | None = None,
    principal: Principal | None = None,
    goal: str | None = None,
    scope: list[str] | None = None,
) -> tuple[GuardSession, contextvars.Token[GuardSession | None]]:
    """Set the current session without a ``with`` block.

    Returns ``(session, token)``; pass the token to :func:`pop_session`
    when the session ends so the previous context is restored correctly.

    Prefer :func:`session_scope` for ordinary ``with`` usage; use this
    pair only when the start and end are separated across control-flow
    boundaries (e.g. an imperative agent loop).
    """
    sid = session_id or (principal.session_id if principal else str(uuid.uuid4()))
    if principal is None:
        principal = Principal(agent_id="sdk-default", session_id=sid)
    session = GuardSession(
        session_id=sid, principal=principal, goal=goal, scope=list(scope or [])
    )
    token = _current.set(session)
    return session, token


def pop_session(token: "contextvars.Token[GuardSession | None]") -> None:
    """Restore the context that existed before the matching :func:`push_session` call."""
    _current.reset(token)


# ── context-manager variant (unchanged public API) ────────────────────────────

@contextlib.contextmanager
def session_scope(
    *,
    session_id: str | None = None,
    principal: Principal | None = None,
    goal: str | None = None,
    scope: list[str] | None = None,
) -> Iterator[GuardSession]:
    """Push a new GuardSession for the duration of the block."""
    session, token = push_session(
        session_id=session_id, principal=principal, goal=goal, scope=scope
    )
    try:
        yield session
    finally:
        pop_session(token)
