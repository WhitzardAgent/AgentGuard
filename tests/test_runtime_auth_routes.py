from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.auth.models import AuthContext


class FakeBroker:
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

