from __future__ import annotations

from agentguard.config_api import ClientConfigAPIServer
from agentguard.guard import AgentGuard
from agentguard.compat import Guard, Principal
from agentguard.u_guard.remote_client import RemoteGuardClient
import pytest


def test_python_client_closes_dpop_runtime_session_by_default(monkeypatch):
    calls: list[str] = []

    def fake_close_runtime_session(self: RemoteGuardClient):
        calls.append(self.session_id or "")
        return {"status": "closed"}

    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", fake_close_runtime_session)
    monkeypatch.setattr(RemoteGuardClient, "unregister_session", lambda self: (_ for _ in ()).throw(AssertionError()))

    guard = AgentGuard(
        "ags-runtime",
        server_url="http://server.test",
        session_token="runtime-token",
        dpop_proof_factory=lambda method, url, access_token=None: "proof",
        use_dpop_auth=True,
        auto_register_session=False,
    )

    guard.close()

    assert calls == ["ags-runtime"]


def test_python_client_can_keep_dpop_runtime_session_open_on_close(monkeypatch):
    monkeypatch.setattr(
        RemoteGuardClient,
        "close_runtime_session",
        lambda self: (_ for _ in ()).throw(AssertionError("runtime session should stay open")),
    )
    monkeypatch.setattr(RemoteGuardClient, "unregister_session", lambda self: (_ for _ in ()).throw(AssertionError()))

    guard = AgentGuard(
        "ags-runtime",
        server_url="http://server.test",
        session_token="runtime-token",
        dpop_proof_factory=lambda method, url, access_token=None: "proof",
        use_dpop_auth=True,
        auto_register_session=False,
        auto_close_runtime_session=False,
    )

    guard.close()


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
    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", lambda self: {"status": "ok"})

    guard = AgentGuard(
        "sess-py-1",
        server_url="http://server.test",
        agent_id="agent-py-1",
        user_id="user-py-1",
        session_token="runtime-token-py-1",
        dpop_proof_factory=lambda method, url, access_token=None: "proof",
        use_dpop_auth=True,
        legacy_identity_headers=False,
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


def test_python_client_defaults_agent_id_to_session_id_when_missing():
    guard = AgentGuard("sess-py-fallback")

    assert guard.context.session_id == "sess-py-fallback"
    assert guard.context.agent_id == "sess-py-fallback"


def test_python_client_rejects_legacy_remote_session_registration():
    with pytest.raises(ValueError, match="ticket/runtime-auth"):
        AgentGuard(
            "sess-py-legacy-remote",
            server_url="http://server.test",
            agent_id="agent-py-legacy-remote",
            user_id="user-py-legacy-remote",
        )


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
    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", lambda self: {"status": "ok"})

    guard = AgentGuard(
        "sess-py-2",
        server_url="http://server.test",
        agent_id="agent-py-2",
        user_id="user-py-2",
        session_token="runtime-token-py-2",
        dpop_proof_factory=lambda method, url, access_token=None: "proof",
        use_dpop_auth=True,
        legacy_identity_headers=False,
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


def test_python_client_registers_advertised_config_api_urls(monkeypatch):
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
    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", lambda self: {"status": "ok"})

    guard = AgentGuard(
        "sess-py-advertised",
        server_url="http://server.test",
        agent_id="agent-py-advertised",
        user_id="user-py-advertised",
        session_token="runtime-token-py-advertised",
        dpop_proof_factory=lambda method, url, access_token=None: "proof",
        use_dpop_auth=True,
        legacy_identity_headers=False,
        client_config_api_host="0.0.0.0",
        client_config_api_port=39001,
        client_config_api_advertise_host="host.docker.internal",
        client_config_api_advertise_port=39001,
    )
    try:
        assert len(calls) == 1
        context = calls[0]
        assert context["metadata"]["client_config_url"] == "http://host.docker.internal:39001/v1/client/plugins/config"
        assert context["metadata"]["client_plugin_list_url"] == "http://host.docker.internal:39001/v1/client/plugins/list"
        assert context["metadata"]["client_health_url"] == "http://host.docker.internal:39001/v1/client/health"
    finally:
        guard.close()


def test_python_client_replays_registered_tools_after_remote_resync(monkeypatch):
    register_calls: list[dict] = []
    report_calls: list[tuple[dict, dict]] = []
    allow_reports = False

    def fake_start(self: ClientConfigAPIServer) -> str:
        if self.port == 0:
            self.port = 43123
        return self.plugin_config_url

    def fake_register(self: RemoteGuardClient, context):
        payload = context.to_dict()
        register_calls.append(payload)
        return {"status": "ok", "session": payload}

    def fake_report(self: RemoteGuardClient, context, tool):
        nonlocal allow_reports
        if not allow_reports:
            raise RuntimeError("temporary tool report failure")
        payload = context.to_dict()
        report_calls.append((payload, dict(tool)))
        return {"status": "ok", "tool": tool}

    monkeypatch.setattr(ClientConfigAPIServer, "start", fake_start)
    monkeypatch.setattr(RemoteGuardClient, "register_session", fake_register)
    monkeypatch.setattr(RemoteGuardClient, "report_tool", fake_report)
    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", lambda self: {"status": "ok"})

    guard = AgentGuard(
        "sess-py-tool-replay",
        server_url="http://server.test",
        agent_id="agent-py-tool-replay",
        user_id="user-py-tool-replay",
        session_token="runtime-token-py-tool-replay",
        dpop_proof_factory=lambda method, url, access_token=None: "proof",
        use_dpop_auth=True,
        legacy_identity_headers=False,
    )
    try:
        guard.wrap_tool(lambda query: query.upper(), name="lookup", capabilities=["read_file"])
        assert report_calls == []

        allow_reports = True
        guard.stop_config_api()
        guard.start_config_api(port=43124)

        assert len(register_calls) >= 2
        assert len(report_calls) == 1
        context, tool = report_calls[0]
        assert context["agent_id"] == "agent-py-tool-replay"
        assert tool["name"] == "lookup"
        assert tool["input_params"] == ["query"]
        assert tool["capabilities"] == ["read_file"]
    finally:
        guard.close()


def test_compat_guard_ticket_creates_langchain_dpop_runtime_session(monkeypatch):
    create_calls: list[dict] = []
    register_calls: list[dict] = []

    def fake_create_runtime_session(self: RemoteGuardClient, body, *, extra_headers_factory=None):
        create_calls.append(
            {
                "body": dict(body),
                "use_dpop_auth": self.use_dpop_auth,
                "legacy_identity_headers": self.legacy_identity_headers,
                "has_dpop_factory": callable(self.dpop_proof_factory),
            }
        )
        return {
            "session_id": "ags_langchain_created",
            "agent_id": "ag_langchain_created",
            "user_id": "7",
            "session_token": "runtime-token",
            "expires_at": 1000,
        }

    def fake_register_session(self: RemoteGuardClient, context):
        register_calls.append(context.to_dict())
        return {"status": "ok"}

    monkeypatch.setattr(RemoteGuardClient, "create_runtime_session", fake_create_runtime_session)
    monkeypatch.setattr(RemoteGuardClient, "register_session", fake_register_session)
    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", lambda self: {"status": "ok"})

    guard = Guard(
        remote_url="http://server.test",
        ticket="agt-ticket",
        mode="enforce",
    )
    guard.start(
        principal=Principal(
            session_id="client-bootstrap-session",
            agent_id="client-self-agent",
            user_id="client-self-user",
        ),
        goal="langchain ticket demo",
    )
    try:
        assert create_calls == [
            {
                "body": {
                    "provider": "langchain",
                    "user_ticket": "agt-ticket",
                    "metadata": {
                        "principal": {
                            "session_id": "client-bootstrap-session",
                            "agent_id": "client-self-agent",
                            "user_id": "client-self-user",
                        },
                        "goal": "langchain ticket demo",
                    },
                },
                "use_dpop_auth": True,
                "legacy_identity_headers": False,
                "has_dpop_factory": True,
            }
        ]
        assert register_calls == []
        remote = guard._require_guard()._remote
        assert remote.session_id == "ags_langchain_created"
        assert remote.agent_id == "ag_langchain_created"
        assert remote.user_id == "7"
        assert remote.session_token == "runtime-token"
        assert remote.use_dpop_auth is True
        assert remote.legacy_identity_headers is False
        headers = remote._headers(method="POST", url="http://server.test/v1/server/guard/decide")
        assert headers["Authorization"] == "DPoP runtime-token"
        assert "DPoP" in headers
        assert "X-AgentGuard-Session-Id" not in headers
        assert "X-AgentGuard-Agent-Id" not in headers
        assert "X-AgentGuard-User-Id" not in headers
    finally:
        guard.close()


def test_compat_guard_remote_langchain_requires_ticket(monkeypatch):
    monkeypatch.setattr(RemoteGuardClient, "register_session", lambda self, context: {"status": "ok"})
    monkeypatch.setattr(RemoteGuardClient, "close_runtime_session", lambda self: {"status": "ok"})

    guard = Guard(remote_url="http://server.test")
    with pytest.raises(ValueError, match="ticket or user_ticket is required"):
        guard.start(
            principal=Principal(
                session_id="legacy-langchain-session",
                agent_id="legacy-langchain-agent",
            )
        )
