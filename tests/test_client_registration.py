from __future__ import annotations

from agentguard.config_api import ClientConfigAPIServer
from agentguard.guard import AgentGuard
from agentguard.u_guard.remote_client import RemoteGuardClient


def test_python_client_registers_remote_session_once_on_init(monkeypatch):
    calls: list[dict] = []

    def fake_start(self: ClientConfigAPIServer) -> str:
        if self.port == 0:
            self.port = 43123
        return self.plugin_config_url

    def fake_register(self: RemoteGuardClient, context):
        payload = context.to_dict()
        calls.append(payload)
        return {"status": "ok", "session": payload}

    monkeypatch.setattr(ClientConfigAPIServer, "start", fake_start)
    monkeypatch.setattr(RemoteGuardClient, "register_session", fake_register)
    monkeypatch.setattr(RemoteGuardClient, "unregister_session", lambda self: {"status": "ok"})

    guard = AgentGuard(
        "sess-py-1",
        server_url="http://server.test",
        agent_id="agent-py-1",
        user_id="user-py-1",
    )
    try:
        assert len(calls) == 1
        context = calls[0]
        assert context["session_id"] == "sess-py-1"
        assert context["agent_id"] == "agent-py-1"
        assert context["user_id"] == "user-py-1"
        assert context["metadata"]["client_config_url"] == "http://127.0.0.1:43123/v1/client/plugins/config"
        assert context["metadata"]["client_plugin_list_url"] == "http://127.0.0.1:43123/v1/client/plugins/list"
        assert context["metadata"]["client_health_url"] == "http://127.0.0.1:43123/v1/client/health"
    finally:
        guard.close()


def test_python_client_resyncs_session_when_config_api_url_changes(monkeypatch):
    calls: list[dict] = []

    def fake_start(self: ClientConfigAPIServer) -> str:
        if self.port == 0:
            self.port = 43123
        return self.plugin_config_url

    def fake_register(self: RemoteGuardClient, context):
        payload = context.to_dict()
        calls.append(payload)
        return {"status": "ok", "session": payload}

    monkeypatch.setattr(ClientConfigAPIServer, "start", fake_start)
    monkeypatch.setattr(RemoteGuardClient, "register_session", fake_register)
    monkeypatch.setattr(RemoteGuardClient, "unregister_session", lambda self: {"status": "ok"})

    guard = AgentGuard(
        "sess-py-2",
        server_url="http://server.test",
        agent_id="agent-py-2",
        user_id="user-py-2",
    )
    try:
        assert len(calls) == 1
        guard.stop_config_api()
        guard.start_config_api(port=43124)
        assert len(calls) == 2
        assert calls[-1]["metadata"]["client_config_url"] == "http://127.0.0.1:43124/v1/client/plugins/config"
        assert calls[-1]["metadata"]["client_plugin_list_url"] == "http://127.0.0.1:43124/v1/client/plugins/list"
        assert calls[-1]["metadata"]["client_health_url"] == "http://127.0.0.1:43124/v1/client/health"
    finally:
        guard.close()
