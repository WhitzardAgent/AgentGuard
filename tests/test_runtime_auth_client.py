from __future__ import annotations

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

