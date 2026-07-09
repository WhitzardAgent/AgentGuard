from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.auth.models import RuntimeSession
from backend.auth.models import AuthContext


class FakeBroker:
    def create_session(self, **kwargs):
        return SimpleNamespace(
            session=RuntimeSession(
                session_id="ags_dify_created",
                agent_id=kwargs["agent_id"],
                user_id=7,
                provider=kwargs["provider"],
                external_session_id=kwargs.get("external_session_id"),
                external_account_email=kwargs["account_email"],
                dpop_jkt="jkt-created",
                status="active",
            ),
            session_token="runtime-token-created",
            token_jti="rtok-created",
            issued_at=100,
            expires_at=1000,
        )

    def authenticate_runtime_request(self, **kwargs):
        return AuthContext(
            session_id="ags_dify_auth",
            agent_id="dify-agent-chat:app-auth",
            user_id="7",
            token_jti="rtok-auth",
            dpop_jkt="jkt-auth",
            external_provider="dify",
            external_session_id="conversation-auth",
        )


def test_session_create_route_returns_agentguard_runtime_session(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"Authorization": "Bearer test-key", "DPoP": "proof"},
        json={
            "provider": "dify",
            "external_session_id": "conversation-1",
            "agent_id": "ag_canonical",
            "account_email": "alice@example.com",
            "external_user_id": "dify-user-1",
            "metadata": {"app_id": "app-1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_dify_created"
    assert payload["session_token"] == "runtime-token-created"
    assert payload["user_id"] == "7"
    assert payload["agent_id"] == "ag_canonical"


def test_session_create_route_allows_missing_external_session_id(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"Authorization": "Bearer test-key", "DPoP": "proof"},
        json={
            "provider": "dify",
            "agent_id": "ag_canonical",
            "account_email": "alice@example.com",
            "external_user_id": "dify-user-1",
            "metadata": {"app_id": "app-1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_dify_created"
    assert payload["external_session_id"] is None


def test_dpop_runtime_route_overrides_self_reported_context(monkeypatch):
    monkeypatch.setattr("backend.auth.dependencies.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/tools/report",
        headers={"Authorization": "DPoP token", "DPoP": "proof"},
        json={
            "context": {
                "session_id": "self-reported-session",
                "agent_id": "self-reported-agent",
                "user_id": "self-reported-user",
            },
            "tool": {"name": "search"},
        },
    )

    assert response.status_code == 200
    assert response.json()["tool"]["owner_agent_id"] == "dify-agent-chat:app-auth"


def test_dpop_runtime_route_rejects_legacy_identity_headers(monkeypatch):
    monkeypatch.setattr("backend.auth.dependencies.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/tools/report",
        headers={
            "Authorization": "DPoP token",
            "DPoP": "proof",
            "X-AgentGuard-Session-Id": "legacy-session",
        },
        json={"context": {"agent_id": "x"}, "tool": {"name": "search"}},
    )

    assert response.status_code == 400
    assert "legacy identity headers" in response.json()["detail"]


def test_dpop_guard_decide_ignores_body_client_session_key(monkeypatch):
    monkeypatch.setattr("backend.auth.dependencies.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    def payload(client_key: str) -> dict:
        return {
            "request_id": f"req-{client_key}",
            "context": {
                "session_id": "self-reported-session",
                "agent_id": "self-reported-agent",
                "user_id": "self-reported-user",
                "metadata": {"client_session_key": client_key},
            },
            "current_event": {
                "event_type": "tool_invoke",
                "payload": {
                    "tool_name": "daily_chat",
                    "arguments": {"query": "hello"},
                    "capabilities": ["dify_tool"],
                },
                "risk_signals": [],
            },
            "trajectory_window": [],
            "local_signals": [],
        }

    headers = {"Authorization": "DPoP token", "DPoP": "proof"}
    first = client.post("/v1/server/guard/decide", headers=headers, json=payload("legacy-a"))
    second = client.post("/v1/server/guard/decide", headers=headers, json=payload("legacy-b"))

    assert first.status_code == 200
    assert second.status_code == 200
