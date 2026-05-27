"""Shared fixtures for AgentGuard test suite."""

from __future__ import annotations

from typing import Any

import pytest

from agentguard.models.events import EventType, Principal, ProvenanceRef, RuntimeEvent, ToolCall
from agentguard.sdk.guard import Guard


# ──────────────────────────────────────────────────────────────────────────────
# Utility helpers (importable from tests via `from agentguard.tests.conftest import …`)
# ──────────────────────────────────────────────────────────────────────────────

def make_principal(
    *,
    role: str = "default",
    session_id: str = "test-session",
    agent_id: str = "test-agent",
    trust_level: int = 1,
    **extra: Any,
) -> Principal:
    """Return a minimal Principal for testing."""
    return Principal(agent_id=agent_id, session_id=session_id, role=role,
                     trust_level=trust_level, **extra)


def build_event(
    tool_name: str = "shell.exec",
    *,
    args: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    sink_type: str = "shell",
    goal: str = "test goal",
    scope: list[str] | None = None,
    session_id: str = "test-session",
    provenance_refs: list[ProvenanceRef] | None = None,
    event_type: EventType = EventType.TOOL_CALL_ATTEMPT,
    principal: Principal | None = None,
    **extra: Any,
) -> RuntimeEvent:
    """Return a minimal RuntimeEvent for testing (importable utility, not a fixture)."""
    p = principal or make_principal(session_id=session_id)
    return RuntimeEvent(
        event_type=event_type,
        principal=p,
        goal=goal,
        scope=scope or [],
        tool_call=ToolCall(
            tool_name=tool_name,
            args=args or {"cmd": "ls /"},
            target=target or {},
            sink_type=sink_type,  # type: ignore[arg-type]
        ),
        provenance_refs=provenance_refs or [],
        **extra,
    )


# Keep backward-compat alias (non-fixture)
make_event = build_event


def mini_guard(policy_dsl: str = "", *, load_builtin: bool = False) -> Guard:
    """Return a Guard with optional inline DSL policy and no built-in rules by default."""
    return Guard(policy_source=policy_dsl if policy_dsl else None, builtin_rules=load_builtin)


# ──────────────────────────────────────────────────────────────────────────────
# Pytest fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_context():
    """Ensure session context is clean before each test."""
    from agentguard.sdk.context import _current
    token = _current.set(None)
    yield
    _current.reset(token)


@pytest.fixture
def principal() -> Principal:
    return make_principal()


@pytest.fixture
def guard() -> Guard:
    return mini_guard()


@pytest.fixture
def make_ev():
    """Fixture that exposes :func:`make_event` as a callable."""
    return make_event


@pytest.fixture
def make_event(principal: Principal):
    """Legacy fixture — prefers the principal fixture for backward compatibility."""
    def _make(
        tool_name: str = "shell.exec",
        args: dict[str, Any] | None = None,
        sink_type: str = "shell",
        event_type: EventType = EventType.TOOL_CALL_ATTEMPT,
        target: dict[str, Any] | None = None,
        provenance_refs: list[ProvenanceRef] | None = None,
        scope: list[str] | None = None,
        goal: str = "test goal",
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=event_type,
            principal=principal,
            goal=goal,
            scope=scope or [],
            tool_call=ToolCall(
                tool_name=tool_name,
                args=args or {"cmd": "ls /"},
                target=target or {},
                sink_type=sink_type,
            ),
            provenance_refs=provenance_refs or [],
        )
    return _make
