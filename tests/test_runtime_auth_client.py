from __future__ import annotations

from agentguard.schemas.context import RuntimeContext
from agentguard.u_guard.remote_client import RemoteGuardClient


def test_remote_client_dpop_mode_omits_legacy_identity_headers():
    client = RemoteGuardClient(
        "http://agentguard.test",
        session_id="legacy-session",
        agent_id="legacy-agent",
        user_id="legacy-user",
        session_key="legacy-key",
        session_token="runtime-token",
        dpop_proof_factory=lambda method, url, token: f"proof:{method}:{url}:{token}",
        use_dpop_auth=True,
        legacy_identity_headers=False,
    )

    headers = client._headers(method="POST", url="http://agentguard.test/v1/server/guard/decide")

    assert headers["Authorization"] == "DPoP runtime-token"
    assert headers["DPoP"] == "proof:POST:http://agentguard.test/v1/server/guard/decide:runtime-token"
    assert "X-AgentGuard-Session-Id" not in headers
    assert "X-AgentGuard-Agent-Id" not in headers
    assert "X-AgentGuard-User-Id" not in headers
    assert "X-AgentGuard-Session-Key" not in headers


def test_remote_client_dpop_create_session_uses_bearer_key_without_access_token():
    client = RemoteGuardClient(
        "http://agentguard.test",
        api_key="sk-test",
        dpop_proof_factory=lambda method, url, token: f"proof:{method}:{url}:{token}",
        use_dpop_auth=True,
        legacy_identity_headers=False,
    )

    headers = client._headers(method="POST", url="http://agentguard.test/v1/server/session/create")

    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["DPoP"] == "proof:POST:http://agentguard.test/v1/server/session/create:None"
    assert "X-AgentGuard-Session-Id" not in headers


def test_remote_client_rejects_protected_calls_without_runtime_auth():
    client = RemoteGuardClient("http://agentguard.test", session_id="legacy-session", agent_id="legacy-agent")

    try:
        client.fetch_snapshot()
    except Exception as exc:
        assert "runtime-auth session_token" in str(exc)
    else:
        raise AssertionError("expected protected remote call to require runtime-auth")


def test_remote_client_allows_catalog_tool_sync_with_adapter_key(monkeypatch):
    sent = []

    def fake_post(self, path, body):
        sent.append((path, body))
        return {"status": "ok", "tool_count": len(body["tools"])}

    monkeypatch.setattr(RemoteGuardClient, "_post", fake_post)
    client = RemoteGuardClient("http://agentguard.test", api_key="sk-test")
    context = RuntimeContext(
        session_id="catalog-session",
        agent_id="catalog-agent",
        metadata={"catalog_sync": True},
    )

    result = client.sync_tools(context, [{"name": "search"}])

    assert result == {"status": "ok", "tool_count": 1}
    assert sent[0][0] == "/v1/server/tools/sync"
