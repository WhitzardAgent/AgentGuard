from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import frontend.app as frontend_app


class _ThreadedServer:
    def __init__(self, handler_cls: type[BaseHTTPRequestHandler]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "_ThreadedServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@contextmanager
def patched_proxy_target(base_url: str, api_key: str = ""):
    old_base = frontend_app.API_BASE_URL
    old_key = frontend_app.API_KEY
    frontend_app.API_BASE_URL = base_url
    frontend_app.API_KEY = api_key
    try:
        yield
    finally:
        frontend_app.API_BASE_URL = old_base
        frontend_app.API_KEY = old_key


@contextmanager
def patched_mock_mode(enabled: bool = True):
    old_value = frontend_app.USE_MOCK_BACKEND
    frontend_app.USE_MOCK_BACKEND = enabled
    frontend_app.MOCK_BACKEND.reset()
    try:
        yield
    finally:
        frontend_app.MOCK_BACKEND.reset()
        frontend_app.USE_MOCK_BACKEND = old_value


def _json_request(method: str, base_url: str, path: str, body: dict | None = None) -> tuple[int, object]:
    conn = http.client.HTTPConnection("127.0.0.1", int(base_url.rsplit(":", 1)[1]), timeout=5)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    parsed = json.loads(raw.decode("utf-8"))
    return response.status, parsed


def _text_request(method: str, base_url: str, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", int(base_url.rsplit(":", 1)[1]), timeout=5)
    conn.request(method, path)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, raw.decode("utf-8")


def test_rules_proxy_forwards_api_key_and_payload():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"loaded": 2}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/rules/reload",
                    {"source": "RULE test\nTRACE: A\nCONDITION: A.name == \"email.send\"\nPOLICY: DENY"},
                )

    assert status == 200
    assert payload == {"loaded": 2}
    assert observed["path"] == "/v1/backend/rules/reload"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"]))["source"].startswith("RULE test")


def test_rules_check_proxy_forwards_api_key_and_payload():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "rule_count": 1, "errors": [], "warnings": [], "hints": [], "source_file": ""}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/rules/check",
                    {"source": "RULE: test\nTRACE: A -> B\nCONDITION: A.name == \"email.send\"\nPOLICY: DENY"},
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/rules/check"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"]))["source"].startswith("RULE: test")


def test_rules_proxy_lists_active_rules():
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps([
                {
                    "id": "rule_one",
                    "name": "rule_one",
                    "status": "published",
                    "rule_id": "rule_one",
                    "tool_pattern": "email.send",
                    "action": "deny",
                    "version": "v1",
                    "pack_id": "__default__",
                    "user_managed": False,
                    "source": "RULE rule_one\nTRACE: A -> B\nCONDITION: A.name == \"email.send\"\nPOLICY: DENY",
                }
            ]).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request("GET", preview.url, "/api/rules")

    assert status == 200
    assert payload == [
        {
            "id": "rule_one",
            "name": "rule_one",
            "status": "published",
            "rule_id": "rule_one",
            "tool_pattern": "email.send",
            "action": "deny",
            "version": "v1",
            "pack_id": "__default__",
            "user_managed": False,
            "source": "RULE rule_one\nTRACE: A -> B\nCONDITION: A.name == \"email.send\"\nPOLICY: DENY",
        }
    ]


def test_agent_rules_proxy_lists_effective_rules():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            body = json.dumps([
                {
                    "id": "agent_rule",
                    "name": "agent_rule",
                    "status": "published",
                    "rule_id": "agent_rule",
                    "tool_pattern": "shell.exec",
                    "action": "deny",
                    "version": "v1",
                    "pack_id": "__default__",
                    "user_managed": False,
                    "source": "RULE: agent_rule\nTRACE: A -> B\nCONDITION: A.name == \"shell.exec\"\nPOLICY: DENY",
                }
            ]).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request("GET", preview.url, "/api/agents/agent-a/rules")

    assert status == 200
    assert payload[0]["rule_id"] == "agent_rule"
    assert observed["path"] == "/v1/backend/agents/agent-a/rules"


def test_agent_rule_create_proxy_forwards_payload_and_api_key():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "created": True}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/agents/agent-a/rules",
                    {"source": 'RULE: agent_rule\nTRACE: A -> B\nCONDITION: A.name == "shell.exec"\nPOLICY: DENY'},
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/rules"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"]))["source"].startswith("RULE: agent_rule")


def test_agent_rule_generate_proxy_forwards_payload_and_api_key():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "candidate": {"summary": "generated"}}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/agents/agent-a/rules/generate",
                    {"requirement": "限制对外发邮件", "max_rounds": 3},
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/rules/generate"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"]))["requirement"] == "限制对外发邮件"


def test_agent_rule_generate_proxy_forwards_llm_config():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "candidate": {"summary": "generated"}}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    request_body = {
        "requirement": "Require review for external requests",
        "max_rounds": 3,
        "llm_config": {
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
        },
    }

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/agents/agent-a/rules/generate",
                    request_body,
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/rules/generate"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"])) == request_body


def test_agent_rule_delete_proxy_forwards_request():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_DELETE(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "DELETE",
                    preview.url,
                    "/api/agents/agent-a/rules/agent_rule",
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/rules/agent_rule"
    assert observed["api_key"] == "test-secret"


def test_tool_label_patch_proxy_forwards_request():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_PATCH(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "tool": {"name": "email.send"}}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "PATCH",
                    preview.url,
                    "/api/agents/agent-a/tools/email.send/labels",
                    {"boundary": "internal", "sensitivity": "low", "integrity": "trusted", "tags": ["manual"]},
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/tools/email.send/labels"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"]))["boundary"] == "internal"


def test_plugins_config_proxy_forwards_payload_and_api_key():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"status": "ok", "loaded_plugins": ["tool_invoke"], "client_updates": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    request_body = {
        "config": {"phases": {"tool_before": {"client": [], "server": ["tool_invoke"]}}},
        "client_principals": [{"agent_id": "agent-a"}],
    }

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request("POST", preview.url, "/api/plugins/config", request_body)

    assert status == 200
    assert payload["status"] == "ok"
    assert observed["path"] == "/v1/backend/plugins/config"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"])) == request_body


def test_agent_plugin_config_get_proxy_forwards_request():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            body = json.dumps({
                "agent_id": "agent-a",
                "plugin_config": {"phases": {}},
                "config_source": "server_default",
            }).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "GET",
                    preview.url,
                    "/api/agents/agent-a/plugins/config",
                )

    assert status == 200
    assert payload["agent_id"] == "agent-a"
    assert payload["config_source"] == "server_default"
    assert observed["path"] == "/v1/backend/agents/agent-a/plugins/config"


def test_agent_plugin_config_post_proxy_forwards_payload_and_api_key():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"status": "ok", "loaded_plugins": [], "client_updates": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    request_body = {
        "config": {"phases": {"tool_before": {"client": [], "server": ["tool_invoke"]}}},
        "client_config": {"phases": {"tool_after": {"client": ["tool_result"], "server": []}}},
    }

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/agents/agent-a/plugins/config",
                    request_body,
                )

    assert status == 200
    assert payload["status"] == "ok"
    assert observed["path"] == "/v1/backend/agents/agent-a/plugins/config"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"])) == request_body


def test_agent_plugin_available_get_proxy_forwards_request():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            body = json.dumps({
                "agent_id": "agent-a",
                "local_plugins": [{"name": "tool_invoke", "description": "", "event_types": ["tool_invoke"], "phases": ["tool_before"]}],
                "remote_plugins": [{"name": "rule_based_plugin", "description": "", "event_types": [], "phases": ["tool_before"]}],
            }).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "GET",
                    preview.url,
                    "/api/agents/agent-a/plugins/available",
                )

    assert status == 200
    assert payload["agent_id"] == "agent-a"
    assert observed["path"] == "/v1/backend/agents/agent-a/plugins/available"


def test_runtime_page_renders_shared_sidebar_and_active_nav():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/runtime.html")

    assert status == 200
    assert 'id="app-sidebar"' in body
    assert 'href="/">Home</a>' in body
    assert 'href="/agents.html">Agents</a>' in body
    assert 'href="/plugins.html"' in body
    assert 'href="/user.html">User</a>' in body
    assert 'href="/runtime.html"' in body
    assert "active" in body
    assert 'href="/labels.html"' in body
    assert 'data-agent-required="true"' in body


def test_home_page_renders_intro_and_home_active_nav():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/")

    assert status == 200
    assert "AgentGuard Home" in body
    assert "AgentGuard" in body
    assert "keeps your agent workflow in control." in body
    assert "DashBoard" in body
    assert 'href="/agents.html"' in body
    assert 'href="/plugins.html"' in body
    assert '<a class="sidebar-nav-item active" href="/">Home</a>' in body
    assert 'href="/labels.html"' in body
    assert 'data-rule-based-required="true"' in body


def test_agents_page_renders_agent_selection_workspace():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/agents.html")

    assert status == 200
    assert "Available Agents" in body
    assert "Choose an agent" in body
    assert '<a class="sidebar-nav-item active" href="/agents.html">Agents</a>' in body


def test_plugins_page_renders_plugin_selection_workspace():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/plugins.html")

    assert status == 200
    assert "Available Plugins" in body
    assert 'href="/plugins.html"' in body
    assert 'Plugins</a>' in body


def test_mock_mode_lists_tools_and_agent_scoped_tools():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            status, tools = _json_request("GET", preview.url, "/api/tools")
            scoped_status, scoped_tools = _json_request("GET", preview.url, "/api/agents/agent-alpha/tools")

    assert status == 200
    assert isinstance(tools, list)
    assert any(item["owner_agent_id"] == "agent-alpha" for item in tools)
    assert scoped_status == 200
    assert {item["owner_agent_id"] for item in scoped_tools} == {"agent-alpha"}
    assert {item["name"] for item in scoped_tools} == {"shell.exec", "email.send", "docs.search"}


def test_mock_mode_lists_global_and_agent_rules():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            status, rules = _json_request("GET", preview.url, "/api/rules")
            scoped_status, scoped_rules = _json_request("GET", preview.url, "/api/agents/agent-beta/rules")

    assert status == 200
    assert isinstance(rules, list)
    assert {rule["rule_id"] for rule in rules} == {"alpha_shell_review", "beta_external_fetch_trace"}
    assert all(rule["user_managed"] is False for rule in rules)
    assert scoped_status == 200
    assert [rule["rule_id"] for rule in scoped_rules] == ["beta_external_fetch_trace"]
    assert scoped_rules[0]["user_managed"] is False


def test_mock_mode_checks_rule_source():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            ok_status, ok_payload = _json_request(
                "POST",
                preview.url,
                "/api/rules/check",
                {"source": 'RULE: sample\nTRACE: A -> B\nCONDITION: A.name == "shell.exec"\nPOLICY: DENY'},
            )
            bad_status, bad_payload = _json_request(
                "POST",
                preview.url,
                "/api/rules/check",
                {"source": "RULE: broken\nTRACE: A -> B\nPOLICY: DENY"},
            )

    assert ok_status == 200
    assert ok_payload["ok"] is True
    assert ok_payload["rule_count"] == 1
    assert isinstance(ok_payload["warnings"], list)
    assert bad_status == 200
    assert bad_payload["ok"] is False
    assert bad_payload["errors"]


def test_mock_mode_reload_updates_published_rules():
    source = "\n\n".join([
        "\n".join([
            "RULE: alpha_email_guard",
            "TRACE: A -> B",
            "ON: tool_call(email.send)",
            'CONDITION: A.name == "email.send"',
            "POLICY: DENY",
            "Severity: medium",
            "Category: outbound",
            'Reason: "Block outbound email in preview"',
        ]),
        "\n".join([
            "RULE: beta_query_review",
            "TRACE: A -> B",
            'CONDITION: A.name == "db.query"',
            "POLICY: HUMAN_CHECK",
        ]),
    ])

    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            reload_status, reload_payload = _json_request(
                "POST",
                preview.url,
                "/api/rules/reload",
                {"source": source},
            )
            alpha_status, alpha_rules = _json_request("GET", preview.url, "/api/agents/agent-alpha/rules")
            beta_status, beta_rules = _json_request("GET", preview.url, "/api/agents/agent-beta/rules")

    assert reload_status == 200
    assert reload_payload == {"ok": True, "loaded": 2}
    assert alpha_status == 200
    assert [rule["rule_id"] for rule in alpha_rules] == ["alpha_email_guard"]
    assert alpha_rules[0]["tool_pattern"] == "email.send"
    assert alpha_rules[0]["user_managed"] is True
    assert beta_status == 200
    assert [rule["rule_id"] for rule in beta_rules] == ["beta_query_review"]
    assert beta_rules[0]["user_managed"] is True


def test_mock_mode_supports_agent_scoped_rule_create_and_delete():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            create_status, create_payload = _json_request(
                "POST",
                preview.url,
                "/api/agents/agent-alpha/rules",
                {"source": 'RULE: alpha_agent_only\nTRACE: A -> B\nCONDITION: A.name == "shell.exec"\nPOLICY: DENY'},
            )
            list_status, listed_rules = _json_request("GET", preview.url, "/api/agents/agent-alpha/rules")
            delete_status, delete_payload = _json_request(
                "DELETE",
                preview.url,
                "/api/agents/agent-alpha/rules/alpha_agent_only",
            )
            after_status, after_rules = _json_request("GET", preview.url, "/api/agents/agent-alpha/rules")

    assert create_status == 200
    assert create_payload["created"] is True
    assert create_payload["pack_id"] == "agent::agent-alpha"
    assert list_status == 200
    assert any(rule["rule_id"] == "alpha_agent_only" for rule in listed_rules)
    assert delete_status == 200
    assert delete_payload["rule_id"] == "alpha_agent_only"
    assert after_status == 200
    assert all(rule["rule_id"] != "alpha_agent_only" for rule in after_rules)


def test_mock_mode_supports_agent_tool_label_patch():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            patch_status, patch_payload = _json_request(
                "PATCH",
                preview.url,
                "/api/agents/agent-alpha/tools/email.send/labels",
                {
                    "boundary": "internal",
                    "sensitivity": "low",
                    "integrity": "trusted",
                    "tags": ["manual"],
                },
            )
            list_status, scoped_tools = _json_request("GET", preview.url, "/api/agents/agent-alpha/tools")

    assert patch_status == 200
    assert patch_payload["tool"]["labels"]["boundary"] == "internal"
    assert patch_payload["tool"]["labels"]["tags"] == ["manual"]
    assert list_status == 200
    updated = next(tool for tool in scoped_tools if tool["name"] == "email.send")
    assert updated["labels"]["sensitivity"] == "low"
