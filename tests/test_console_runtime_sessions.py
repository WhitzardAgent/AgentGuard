from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.auth.models import RuntimeSession, RuntimeSessionSummary


class FakeUserStore:
    def user_for_session(self, token: str):
        if token == "admin-session":
            return SimpleNamespace(id=1, profile_json='{"role":"admin"}')
        if token != "session-1":
            return None
        return SimpleNamespace(id=7)

    def list_external_accounts(self, user):
        return []


class FakeAgentStore:
    def __init__(self, agent_ids: set[str]) -> None:
        self.agent_ids = agent_ids

    def agent_ids_for_user(self, user_id: int) -> set[str]:
        return set(self.agent_ids) if user_id == 7 else set()


class FakeRuntimeSessionStore:
    def __init__(self, *, user_id: int = 7) -> None:
        self.session = RuntimeSession(
            session_id="ags_dify_1",
            agent_id="ag_1",
            user_id=user_id,
            provider="dify",
            external_session_id=None,
            external_account_email="alice@example.com",
            dpop_jkt="jkt-1",
            status="active",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_seen_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.closed = False

    def list_sessions(self, *, agent_id: str, user_id: int | None, status: str, limit: int):
        if agent_id != "ag_1":
            return []
        if user_id is not None and user_id != self.session.user_id:
            return []
        session = self.session
        return [
            RuntimeSessionSummary(
                session=session,
                active_token_count=0 if self.closed else 1,
                latest_token_expires_at=datetime(2026, 1, 3, tzinfo=timezone.utc) if not self.closed else None,
                latest_token_issued_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
            )
        ]

    def get_session(self, session_id: str):
        if session_id == self.session.session_id:
            return self.session
        return None

    def close_session(self, session_id: str) -> None:
        if session_id != self.session.session_id:
            return
        self.closed = True
        self.session = RuntimeSession(
            session_id=self.session.session_id,
            agent_id=self.session.agent_id,
            user_id=self.session.user_id,
            provider=self.session.provider,
            external_session_id=self.session.external_session_id,
            external_account_email=self.session.external_account_email,
            dpop_jkt=self.session.dpop_jkt,
            status="closed",
            created_at=self.session.created_at,
            last_seen_at=self.session.last_seen_at,
            closed_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )


def _patch_console_dependencies(monkeypatch, store: FakeRuntimeSessionStore, *, agent_ids: set[str]) -> None:
    monkeypatch.setattr("backend.api.console_router.get_user_store", lambda: FakeUserStore())
    monkeypatch.setattr("backend.api.console_router.AgentStore", lambda: FakeAgentStore(agent_ids))
    monkeypatch.setattr("backend.api.console_router.get_runtime_session_store", lambda: store)


def test_agent_runtime_sessions_are_scoped_to_logged_in_user(monkeypatch):
    store = FakeRuntimeSessionStore()
    _patch_console_dependencies(monkeypatch, store, agent_ids={"ag_1"})
    client = TestClient(create_app())

    response = client.get(
        "/v1/backend/agents/ag_1/runtime/sessions?status=all",
        cookies={"agentguard_user_session": "session-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["session_id"] == "ags_dify_1"
    assert payload[0]["external_session_id"] is None
    assert payload[0]["active_token_count"] == 1


def test_agent_runtime_sessions_reject_unbound_agent(monkeypatch):
    store = FakeRuntimeSessionStore()
    _patch_console_dependencies(monkeypatch, store, agent_ids={"ag_other"})
    client = TestClient(create_app())

    response = client.get(
        "/v1/backend/agents/ag_1/runtime/sessions",
        cookies={"agentguard_user_session": "session-1"},
    )

    assert response.status_code == 403


def test_agent_runtime_session_close_marks_session_closed(monkeypatch):
    store = FakeRuntimeSessionStore()
    _patch_console_dependencies(monkeypatch, store, agent_ids={"ag_1"})
    client = TestClient(create_app())

    response = client.post(
        "/v1/backend/agents/ag_1/runtime/sessions/ags_dify_1/close",
        cookies={"agentguard_user_session": "session-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["session"]["status"] == "closed"
    assert payload["session"]["active_token_count"] == 0


def test_admin_runtime_sessions_can_read_and_close_other_user_sessions(monkeypatch):
    store = FakeRuntimeSessionStore(user_id=9)
    _patch_console_dependencies(monkeypatch, store, agent_ids=set())
    client = TestClient(create_app())

    sessions = client.get(
        "/v1/backend/agents/ag_1/runtime/sessions?status=all",
        cookies={"agentguard_user_session": "admin-session"},
    )

    assert sessions.status_code == 200
    payload = sessions.json()
    assert payload[0]["session_id"] == "ags_dify_1"
    assert payload[0]["user_id"] == "9"

    closed = client.post(
        "/v1/backend/agents/ag_1/runtime/sessions/ags_dify_1/close",
        cookies={"agentguard_user_session": "admin-session"},
    )

    assert closed.status_code == 200
    assert closed.json()["session"]["status"] == "closed"
