from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.agents.store import AgentDeletionResult, AgentRecord
from backend.api.app import create_app


class FakeUserStore:
    def user_for_session(self, token: str):
        if token == "admin-session":
            return SimpleNamespace(id=1, profile_json='{"role":"admin"}')
        return None

    def list_external_accounts(self, user):
        return []


class FakeAgentStore:
    def __init__(self) -> None:
        self.records = {
            "ag_langchain": AgentRecord(
                agent_id="ag_langchain",
                agent_identity_code="agic_langchain",
                provider="langchain",
                external_agent_id="langchain-demo",
                agent_type="agent",
                status="active",
            ),
            "ag_dify": AgentRecord(
                agent_id="ag_dify",
                agent_identity_code="agic_dify",
                provider="dify",
                external_agent_id="app-1",
                agent_type="agent_chat",
                status="active",
            ),
            "ag_openclaw": AgentRecord(
                agent_id="ag_openclaw",
                agent_identity_code="agic_openclaw",
                provider="openclaw",
                external_agent_id="agentguard-emailcase",
                agent_type="agent",
                status="active",
            ),
        }
        self.deleted_agent_ids: list[str] = []

    def list_agents(self, agent_ids=None):
        assert agent_ids is None
        return [
            self.records["ag_dify"],
            self.records["ag_langchain"],
            self.records["ag_openclaw"],
        ]

    def get_agent(self, agent_id: str):
        return self.records.get(agent_id)

    def delete_agent(self, agent_id: str) -> AgentDeletionResult:
        self.deleted_agent_ids.append(agent_id)
        self.records.pop(agent_id, None)
        return AgentDeletionResult(agent_id=agent_id, deleted=True, agent_count=1)


class FakeConsole:
    def __init__(self) -> None:
        self.unregistered_agent_ids: list[str] = []

    def unregister_agent(self, agent_id: str) -> None:
        self.unregistered_agent_ids.append(agent_id)


def _patch_console_agent_dependencies(monkeypatch, store: FakeAgentStore, console: FakeConsole) -> None:
    monkeypatch.setattr("backend.api.console_router.get_user_store", lambda: FakeUserStore())
    monkeypatch.setattr("backend.api.console_router.AgentStore", lambda: store)
    monkeypatch.setattr("backend.api.console_router.get_console", lambda: console)


def test_list_agents_exposes_console_delete_capability(monkeypatch):
    store = FakeAgentStore()
    console = FakeConsole()
    _patch_console_agent_dependencies(monkeypatch, store, console)
    client = TestClient(create_app())

    response = client.get(
        "/v1/backend/agents",
        cookies={"agentguard_user_session": "admin-session"},
    )

    assert response.status_code == 200
    payload = {item["agent_id"]: item for item in response.json()}
    assert payload["ag_langchain"]["can_delete"] is True
    assert payload["ag_dify"]["can_delete"] is True
    assert payload["ag_openclaw"]["can_delete"] is False


def test_delete_agent_allows_dify_local_record_cleanup(monkeypatch):
    store = FakeAgentStore()
    console = FakeConsole()
    _patch_console_agent_dependencies(monkeypatch, store, console)
    client = TestClient(create_app())

    response = client.delete(
        "/v1/backend/agents/ag_dify",
        cookies={"agentguard_user_session": "admin-session"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert store.deleted_agent_ids == ["ag_dify"]
    assert console.unregistered_agent_ids == ["ag_dify"]


def test_delete_agent_rejects_unsupported_provider(monkeypatch):
    store = FakeAgentStore()
    console = FakeConsole()
    _patch_console_agent_dependencies(monkeypatch, store, console)
    client = TestClient(create_app())

    response = client.delete(
        "/v1/backend/agents/ag_openclaw",
        cookies={"agentguard_user_session": "admin-session"},
    )

    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert store.deleted_agent_ids == []
    assert console.unregistered_agent_ids == []
