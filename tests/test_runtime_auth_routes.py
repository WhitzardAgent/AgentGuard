from __future__ import annotations

from types import SimpleNamespace

from backend.api.app import create_app
from backend.auth.models import AuthContext, RuntimeSession
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.auth.models import AuthContext
from backend.auth.models import RuntimeSession
from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager


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

    def bootstrap_openclaw_agents(self, **kwargs):
        agent = SimpleNamespace(
            external_agent_id="main",
            agent_id="ag_openclaw_main",
            agent_identity_code="agic_openclaw_main",
            public_key_thumbprint="thumb-main",
            status="active",
        )
        credential = SimpleNamespace(
            credential_id="agcred_openclaw_main",
            agent_id="ag_openclaw_main",
            public_key_thumbprint="thumb-main",
        )
        return (
            SimpleNamespace(user_id=7, ticket_prefix="agt-ticket"),
            [
                SimpleNamespace(
                    agent=agent,
                    credential=credential,
                    user_id=7,
                    user_binding_created=True,
                    user_binding_updated=False,
                )
            ],
        )

    def bootstrap_opencode_agents(self, **kwargs):
        agent = SimpleNamespace(
            external_agent_id="opencode:agentguard",
            agent_id="ag_opencode_agentguard",
            agent_identity_code="agic_opencode_agentguard",
            public_key_thumbprint="thumb-opencode-agentguard",
            status="active",
        )
        credential = SimpleNamespace(
            credential_id="agcred_opencode_agentguard",
            agent_id="ag_opencode_agentguard",
            public_key_thumbprint="thumb-opencode-agentguard",
        )
        return (
            SimpleNamespace(user_id=7, ticket_prefix="agt-ticket"),
            [
                SimpleNamespace(
                    agent=agent,
                    credential=credential,
                    user_id=7,
                    user_binding_created=True,
                    user_binding_updated=False,
                )
            ],
        )

    def create_openclaw_session(self, **kwargs):
        return SimpleNamespace(
            session=RuntimeSession(
                session_id="ags_openclaw_canonical",
                agent_id=kwargs["agent_id"],
                user_id=7,
                provider="openclaw",
                external_session_id=kwargs.get("external_session_id"),
                external_account_email=None,
                dpop_jkt="jkt-openclaw",
                status="active",
            ),
            session_token="runtime-token-openclaw-canonical",
            token_jti="rtok-openclaw-canonical",
            issued_at=102,
            expires_at=1002,
        )

    def create_opencode_session(self, **kwargs):
        return SimpleNamespace(
            session=RuntimeSession(
                session_id="ags_opencode_canonical",
                agent_id=kwargs["agent_id"],
                user_id=7,
                provider="opencode",
                external_session_id=kwargs.get("external_session_id"),
                external_account_email=None,
                dpop_jkt="jkt-opencode",
                status="active",
            ),
            session_token="runtime-token-opencode-canonical",
            token_jti="rtok-opencode-canonical",
            issued_at=103,
            expires_at=1003,
        )


class FakeLangChainBroker(FakeBroker):
    def create_ticket_session(self, **kwargs):
        provider = kwargs["provider"]
        return SimpleNamespace(
            session=RuntimeSession(
                session_id=f"ags_{provider}_created",
                agent_id=f"ag_{provider}_created",
                user_id=8,
                provider=provider,
                external_session_id=None,
                external_account_email=None,
                dpop_jkt=f"jkt-{provider}",
                status="active",
            ),
            session_token=f"runtime-token-{provider}",
            token_jti=f"rtok-{provider}",
            issued_at=101,
            expires_at=1001,
        )

    def create_langchain_ticket_session(self, **kwargs):
        return self.create_ticket_session(provider="langchain", **kwargs)

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


def test_session_create_route_dispatches_langchain_ticket_provider(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeLangChainBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"DPoP": "proof"},
        json={
            "provider": "langchain",
            "user_ticket": "agt-ticket",
            "metadata": {"name": "LangChain demo"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_langchain_created"
    assert payload["agent_id"] == "ag_langchain_created"
    assert payload["user_id"] == "8"
    assert payload["session_token"] == "runtime-token-langchain"
    assert payload["auth_method"] == "langchain_dpop"


def test_session_create_route_dispatches_openclaw_ticket_provider(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeLangChainBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"DPoP": "proof"},
        json={
            "provider": "openclaw",
            "user_ticket": "agt-ticket",
            "metadata": {"openclaw_agent_id": "main", "openclaw_session_id": "session-1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_openclaw_created"
    assert payload["agent_id"] == "ag_openclaw_created"
    assert payload["user_id"] == "8"
    assert payload["session_token"] == "runtime-token-openclaw"
    assert payload["auth_method"] == "openclaw_dpop"


def test_session_create_route_dispatches_opencode_ticket_provider(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeLangChainBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"DPoP": "proof"},
        json={
            "provider": "opencode",
            "user_ticket": "agt-ticket",
            "metadata": {"opencode_session_id": "session-1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_opencode_created"
    assert payload["agent_id"] == "ag_opencode_created"
    assert payload["user_id"] == "8"
    assert payload["session_token"] == "runtime-token-opencode"
    assert payload["auth_method"] == "opencode_dpop"


def test_agent_bootstrap_route_registers_openclaw_catalog(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/agents/bootstrap",
        json={
            "provider": "openclaw",
            "user_ticket": "agt-ticket",
            "provider_instance_id": "local-openclaw",
            "agents": [
                {
                    "provider": "openclaw",
                    "provider_instance_id": "local-openclaw",
                    "external_agent_id": "main",
                    "agent_type": "agent",
                    "name": "main",
                    "public_key_jwk": {"kty": "OKP", "crv": "Ed25519", "x": "abc"},
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openclaw"
    assert payload["user_id"] == "7"
    assert payload["agents"][0]["external_agent_id"] == "main"
    assert payload["agents"][0]["agent_id"] == "ag_openclaw_main"
    assert payload["agents"][0]["user_bound"] is True


def test_agent_bootstrap_route_registers_opencode_catalog(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/agents/bootstrap",
        json={
            "provider": "opencode",
            "user_ticket": "agt-ticket",
            "provider_instance_id": "local-opencode",
            "agents": [
                {
                    "provider": "opencode",
                    "provider_instance_id": "local-opencode",
                    "external_agent_id": "opencode:agentguard",
                    "agent_type": "agent",
                    "name": "OpenCode agentguard",
                    "public_key_jwk": {"kty": "OKP", "crv": "Ed25519", "x": "abc"},
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "opencode"
    assert payload["user_id"] == "7"
    assert payload["agents"][0]["external_agent_id"] == "opencode:agentguard"
    assert payload["agents"][0]["agent_id"] == "ag_opencode_agentguard"
    assert payload["agents"][0]["user_bound"] is True


def test_session_create_route_dispatches_openclaw_canonical_provider(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"DPoP": "proof", "X-AgentGuard-Agent-Proof": "agent-proof"},
        json={
            "provider": "openclaw",
            "agent_id": "ag_openclaw_main",
            "external_session_id": "agent:main:session-1",
            "metadata": {"openclaw_agent_id": "main"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_openclaw_canonical"
    assert payload["agent_id"] == "ag_openclaw_main"
    assert payload["external_session_id"] == "agent:main:session-1"
    assert payload["session_token"] == "runtime-token-openclaw-canonical"
    assert payload["auth_method"] == "openclaw_dpop"


def test_session_create_route_dispatches_opencode_canonical_provider(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"DPoP": "proof", "X-AgentGuard-Agent-Proof": "agent-proof"},
        json={
            "provider": "opencode",
            "agent_id": "ag_opencode_agentguard",
            "external_session_id": "session-1",
            "metadata": {"opencode_agent": "agentguard"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ags_opencode_canonical"
    assert payload["agent_id"] == "ag_opencode_agentguard"
    assert payload["external_session_id"] == "session-1"
    assert payload["session_token"] == "runtime-token-opencode-canonical"
    assert payload["auth_method"] == "opencode_dpop"


def test_session_create_route_keeps_dify_required_fields(monkeypatch):
    monkeypatch.setattr("backend.api.client_router.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/create",
        headers={"Authorization": "Bearer test-key", "DPoP": "proof"},
        json={
            "provider": "dify",
            "account_email": "alice@example.com",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "agent_id is required"


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


def test_catalog_tool_sync_accepts_adapter_api_key(monkeypatch):
    from backend.api import client_router

    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    original_console = client_router._console
    client_router._console = ConsoleState(RuntimeManager())
    client = TestClient(create_app())
    try:
        response = client.post(
            "/v1/server/tools/sync",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "context": {
                    "session_id": "catalog-session",
                    "agent_id": "catalog-agent",
                    "metadata": {"catalog_sync": True},
                },
                "tools": [{"name": "search", "input_params": ["query"]}],
            },
        )
    finally:
        client_router._console = original_console

    assert response.status_code == 200
    assert response.json()["tool_count"] == 1


def test_non_catalog_tool_sync_still_requires_dpop(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/tools/sync",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "context": {"session_id": "runtime-session", "agent_id": "runtime-agent"},
            "tools": [{"name": "search"}],
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "missing DPoP access token"


def test_dpop_session_register_preserves_body_client_session_key(monkeypatch):
    monkeypatch.setattr("backend.auth.dependencies.get_dify_auth_broker", lambda: FakeBroker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/server/session/register",
        headers={"Authorization": "DPoP token", "DPoP": "proof"},
        json={
            "context": {
                "session_id": "self-reported-session",
                "agent_id": "self-reported-agent",
                "user_id": "self-reported-user",
                "metadata": {
                    "client_session_key": "sk-client-config",
                    "client_plugin_list_url": "http://host.docker.internal:38181/v1/client/plugins/list",
                },
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["client_key"] == "sk-client-config"
    assert payload["session"]["client_plugin_list_url"] == "http://host.docker.internal:38181/v1/client/plugins/list"


def test_runtime_plugin_config_route_returns_effective_agent_config(monkeypatch):
    from backend.api import client_router

    monkeypatch.setattr("backend.auth.dependencies.get_dify_auth_broker", lambda: FakeLangChainBroker())
    original_manager = client_router._manager
    client_router._manager = RuntimeManager()
    client_router._manager.set_agent_plugin_config(
        "dify-agent-chat:app-auth",
        {
            "phases": {
                "llm_before": {"client": [], "server": []},
                "llm_after": {"client": [], "server": []},
                "tool_before": {"client": [], "server": []},
                "tool_after": {"client": [], "server": []},
                "global": {"client": [], "server": []},
            }
        },
        client_config={
            "phases": {
                "llm_before": {"client": ["modify_input_demo"], "server": []},
                "llm_after": {"client": ["modify_output_demo"], "server": []},
                "tool_before": {"client": [], "server": []},
                "tool_after": {"client": [], "server": []},
                "global": {"client": [], "server": []},
            }
        },
    )
    client = TestClient(create_app())
    try:
        response = client.get(
            "/v1/server/session/plugin-config",
            headers={"Authorization": "DPoP token", "DPoP": "proof"},
        )
    finally:
        client_router._manager = original_manager

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["agent_id"] == "dify-agent-chat:app-auth"
    assert payload["session_id"] == "ags_dify_auth"
    assert payload["config_source"] == "agent_override"
    assert payload["plugin_config"]["phases"]["llm_before"]["client"] == ["modify_input_demo"]
    assert payload["plugin_config"]["phases"]["llm_after"]["client"] == ["modify_output_demo"]
