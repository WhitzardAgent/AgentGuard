from __future__ import annotations

import time

from agentguard.adapters.agent.dify_runtime_auth import DifyRuntimeAuthManager


def test_dify_runtime_auth_manager_creates_reuses_and_refreshes(monkeypatch):
    create_calls = []
    refresh_calls = []

    class FakeRemote:
        def __init__(self, *args, **kwargs):
            self.session_token = kwargs.get("session_token")

        def create_runtime_session(self, body):
            create_calls.append(body)
            return {
                "session_id": "ags_dify_1",
                "session_token": "token-1",
                "user_id": "7",
                "expires_at": int(time.time()) + 900,
            }

        def refresh_runtime_session(self):
            refresh_calls.append(self.session_token)
            return {
                "session_id": "ags_dify_1",
                "session_token": "token-2",
                "user_id": "7",
                "expires_at": int(time.time()) + 900,
            }

    monkeypatch.setattr("agentguard.adapters.agent.dify_runtime_auth.RemoteGuardClient", FakeRemote)
    manager = DifyRuntimeAuthManager()

    first = manager.ensure(
        server_url="http://agentguard.test",
        api_key=None,
        agent_id="dify-agent-chat:app-1",
        external_session_id="conversation-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={"app_id": "app-1"},
        timeout_s=1,
        retries=0,
    )
    second = manager.ensure(
        server_url="http://agentguard.test",
        api_key=None,
        agent_id="dify-agent-chat:app-1",
        external_session_id="conversation-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={"app_id": "app-1"},
        timeout_s=1,
        retries=0,
    )
    first.expires_at = int(time.time()) + 1
    refreshed = manager.ensure(
        server_url="http://agentguard.test",
        api_key=None,
        agent_id="dify-agent-chat:app-1",
        external_session_id="conversation-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={"app_id": "app-1"},
        timeout_s=1,
        retries=0,
    )

    assert first is second is refreshed
    assert first.session_id == "ags_dify_1"
    assert first.session_token == "token-2"
    assert first.canonical_user_id == "7"
    assert len(create_calls) == 1
    assert refresh_calls == ["token-1"]


def test_dify_runtime_auth_manager_omits_missing_external_session_id(monkeypatch):
    create_calls = []

    class FakeRemote:
        def __init__(self, *args, **kwargs):
            pass

        def create_runtime_session(self, body):
            create_calls.append(body)
            return {
                "session_id": "ags_dify_internal",
                "session_token": "token-internal",
                "user_id": "7",
                "expires_at": int(time.time()) + 900,
            }

    monkeypatch.setattr("agentguard.adapters.agent.dify_runtime_auth.RemoteGuardClient", FakeRemote)
    manager = DifyRuntimeAuthManager()

    first = manager.ensure(
        server_url="http://agentguard.test",
        api_key=None,
        agent_id="ag_workflow",
        external_session_id=None,
        cache_key="agentguard-internal:dify:workflow:app-1:user-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={"app_id": "app-1"},
        timeout_s=1,
        retries=0,
    )
    second = manager.ensure(
        server_url="http://agentguard.test",
        api_key=None,
        agent_id="ag_workflow",
        external_session_id=None,
        cache_key="agentguard-internal:dify:workflow:app-1:user-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={"app_id": "app-1"},
        timeout_s=1,
        retries=0,
    )

    assert first is second
    assert first.session_id == "ags_dify_internal"
    assert len(create_calls) == 1
    assert "external_session_id" not in create_calls[0]
