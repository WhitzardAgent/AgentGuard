from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from backend.api.app import create_app


@dataclass(frozen=True)
class FakeMapping:
    provider: str
    external_session_id: str
    agent_id: str
    agentguard_session_id: str
    user_id: int | None = None
    external_user_id: str | None = None
    account_email: str | None = None


class FakeSessionMappingStore:
    def __init__(self) -> None:
        self.by_key: dict[tuple[str, str, str], FakeMapping] = {}

    def get_or_create(
        self,
        *,
        provider: str,
        external_session_id: str,
        agent_id: str,
        user_id: int | None = None,
        external_user_id: str | None = None,
        account_email: str | None = None,
        metadata: dict | None = None,
    ) -> FakeMapping:
        key = (provider, external_session_id, agent_id)
        if key not in self.by_key:
            self.by_key[key] = FakeMapping(
                provider=provider,
                external_session_id=external_session_id,
                agent_id=agent_id,
                agentguard_session_id="ags_dify_test_session",
                user_id=user_id,
                external_user_id=external_user_id,
                account_email=account_email,
            )
        return self.by_key[key]


def test_external_session_map_endpoint_returns_stable_agentguard_session(monkeypatch):
    store = FakeSessionMappingStore()
    monkeypatch.setattr("backend.api.client_router.get_session_mapping_store", lambda: store)
    monkeypatch.setattr(
        "backend.api.client_router._external_account_user_id",
        lambda provider, account_email: 7 if (provider, account_email) == ("dify", "alice@example.com") else None,
    )
    client = TestClient(create_app())

    payload = {
        "provider": "dify",
        "external_session_id": "conversation-1",
        "agent_id": "dify-agent-chat:app-1",
        "external_user_id": "dify-user-1",
        "account_email": "alice@example.com",
        "metadata": {"app_id": "app-1"},
    }
    first = client.post("/v1/server/session/map", json=payload)
    second = client.post("/v1/server/session/map", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["agentguard_session_id"] == "ags_dify_test_session"
    assert second.json()["agentguard_session_id"] == "ags_dify_test_session"
    assert first.json()["external_session_id"] == "conversation-1"
    assert first.json()["user_id"] == 7
