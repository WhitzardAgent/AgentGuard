from __future__ import annotations

import pytest

from agentguard import Principal
from agentguard.sdk.client import RemoteGuardClient
from agentguard.sdk.guard import Guard


def test_remote_guard_tool_decorator_reports_registration(monkeypatch):
    reported = []

    def fake_upsert(self, entry):
        reported.append(entry)
        return True

    monkeypatch.setattr(RemoteGuardClient, "upsert_tool", fake_upsert)
    guard = Guard(remote_url="http://runtime.example", api_key="secret")
    guard.start(principal=Principal(agent_id="agent-a", session_id="sess-a"))

    @guard.tool(
        "email.send",
        boundary="external",
        sensitivity="high",
        integrity="trusted",
        tags=["finance"],
    )
    def send_email(to: str, subject: str) -> str:
        return f"sent to {to}: {subject}"

    assert "email.send" in guard.registry
    assert send_email is guard.registry["email.send"]
    assert len(reported) == 1
    assert reported[0].owner_agent_id == "agent-a"
    assert reported[0].name == "email.send"
    assert reported[0].labels.boundary == "external"
    assert reported[0].input_params == ["to", "subject"]
    guard.close()


def test_remote_guard_register_reports_registration(monkeypatch):
    reported = []

    def fake_upsert(self, entry):
        reported.append(entry)
        return True

    monkeypatch.setattr(RemoteGuardClient, "upsert_tool", fake_upsert)
    guard = Guard(remote_url="http://runtime.example", api_key="secret")
    guard.start(principal=Principal(agent_id="agent-b", session_id="sess-b"))

    def query(sql: str, limit: int = 10) -> dict[str, int]:
        return {"rows": limit}

    wrapped = guard.register(
        "db.query",
        query,
        boundary="internal",
        sensitivity="moderate",
        integrity="trusted",
        tags=["analytics"],
    )

    assert wrapped is guard.registry["db.query"]
    assert len(reported) == 1
    assert reported[0].owner_agent_id == "agent-b"
    assert reported[0].name == "db.query"
    assert reported[0].labels.sensitivity == "moderate"
    assert reported[0].input_params == ["sql", "limit"]
    guard.close()


def test_remote_registration_failure_does_not_block_local_registration(monkeypatch):
    def fake_upsert(self, entry):
        raise RuntimeError("network down")

    monkeypatch.setattr(RemoteGuardClient, "upsert_tool", fake_upsert)
    guard = Guard(remote_url="http://runtime.example", api_key="secret")
    guard.start(principal=Principal(agent_id="agent-c", session_id="sess-c"))

    @guard.tool("shell.exec")
    def shell_exec(cmd: str) -> str:
        return cmd

    assert "shell.exec" in guard.registry
    assert shell_exec("echo ok") == "echo ok"
    guard.close()


def test_remote_registration_without_active_session_fails(monkeypatch):
    monkeypatch.setattr(RemoteGuardClient, "upsert_tool", lambda self, entry: True)
    guard = Guard(remote_url="http://runtime.example", api_key="secret")

    with pytest.raises(RuntimeError, match="active Guard session"):
        @guard.tool("shell.exec")
        def shell_exec(cmd: str) -> str:
            return cmd


def test_local_guard_does_not_report_tool_registration(monkeypatch):
    calls = []

    def fake_upsert(self, entry):
        calls.append(entry)
        return True

    monkeypatch.setattr(RemoteGuardClient, "upsert_tool", fake_upsert)
    guard = Guard()

    @guard.tool("local.tool")
    def local_tool(x: int) -> int:
        return x

    assert "local.tool" in guard.registry
    assert local_tool(3) == 3
    assert calls == []
