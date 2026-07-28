from __future__ import annotations

import http.client
import json
import sys
import threading
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import frontend.app as frontend_app  # noqa: E402


class _ThreadedServer:
    def __init__(self, handler_cls: type[BaseHTTPRequestHandler]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> _ThreadedServer:
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


def _raw_request(
    method: str,
    base_url: str,
    path: str,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, list[str]], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", int(base_url.rsplit(":", 1)[1]), timeout=5)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=request_headers)
    response = conn.getresponse()
    raw = response.read()
    response_headers: dict[str, list[str]] = {}
    for key, value in response.getheaders():
        response_headers.setdefault(key.lower(), []).append(value)
    conn.close()
    return response.status, response_headers, raw


def _text_request(method: str, base_url: str, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", int(base_url.rsplit(":", 1)[1]), timeout=5)
    conn.request(method, path)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, raw.decode("utf-8")


def test_preview_server_uses_http_11_for_page_responses():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        conn = http.client.HTTPConnection("127.0.0.1", int(preview.url.rsplit(":", 1)[1]), timeout=5)
        conn.request("GET", "/login")
        response = conn.getresponse()
        body = response.read()
        version = response.version
        conn.close()

    assert response.status == 200
    assert version == 11
    assert body


def test_static_assets_include_cache_headers():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, headers, body = _raw_request("GET", preview.url, "/static/common/styles.css")

    assert status == 200
    assert body
    assert headers.get("cache-control") == ["public, max-age=300"]
    assert "last-modified" in headers


def test_i18n_script_disables_stale_caching():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, headers, body = _raw_request("GET", preview.url, "/static/common/i18n.js")

    assert status == 200
    assert body
    assert headers.get("cache-control") == ["no-cache, must-revalidate"]
    assert "last-modified" in headers


def test_html_pages_disable_stale_caching():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, headers, body = _raw_request("GET", preview.url, "/runtime")

    assert status == 200
    assert body
    assert headers.get("cache-control") == ["no-cache"]
    assert "last-modified" in headers


def test_rules_page_uses_single_bundle_script():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/rules")

    assert status == 200
    assert '/static/bundles/rules-page.js' in body
    assert '/static/pages/rules/rules.js' not in body
    assert '/static/common/app.js' not in body


def test_security_audit_page_is_available():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/security-audit")

    assert status == 200
    assert "Agent Security Audit - AgentGuard" in body
    assert "/static/pages/security-audit/security-audit.js" in body
    assert 'href="/security-audit.html"' in body
    assert 'id="security-audit-selected-agent"' in body
    assert 'id="security-audit-settings-open"' in body
    assert 'id="security-audit-llm-api-key" type="password"' in body
    assert "Saved only in this browser" in body
    assert 'id="security-audit-agent"' not in body
    assert "Created (Beijing time)" in body
    assert body.index('href="/mcps.html"') < body.index('href="/security-audit.html"')
    assert 'href="/security-audit.html" data-agent-required="true" data-admin-required="true"' in body


def test_security_audit_page_renders_chinese_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/security-audit.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert "智能体安全审计" in body
    assert "开始新的审计" in body
    assert "开始审计" in body
    assert "安全发现" in body
    assert "创建时间（北京时间）" in body


def test_security_audit_javascript_uses_beijing_time_and_verification_labels():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request(
            "GET",
            preview.url,
            "/static/pages/security-audit/security-audit.js",
        )

    assert status == 200
    assert 'timeZone: "Asia/Shanghai"' in body
    assert "Number(hour) - 8" in body
    assert 'finding.verification === "confirmed"' in body
    assert 'agentguard.securityAuditLlmConfig' in body
    assert 'body.llm_config = llmConfig' in body
    assert 'elements.auditor.value !== "rule_agent_security"' in body


def test_security_audit_proxy_forwards_query_and_payload():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["get_path"] = self.path
            body = json.dumps({"runs": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            observed["post_path"] = self.path
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"run_id": "audit-1", "status": "queued"}).encode("utf-8")
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                get_status, get_payload = _json_request(
                    "GET",
                    preview.url,
                    "/api/security-audits?agent_id=agent-alpha&limit=10",
                )
                post_status, post_payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/security-audits",
                    {"agent_id": "agent-alpha", "auditor_name": "rule_agent_security"},
                )

    assert get_status == 200
    assert get_payload == {"runs": []}
    assert observed["get_path"] == "/v1/backend/security-audits?agent_id=agent-alpha&limit=10"
    # The preview proxy intentionally normalizes successful upstream responses to 200.
    assert post_status == 200
    assert post_payload == {"run_id": "audit-1", "status": "queued"}
    assert observed["post_path"] == "/v1/backend/security-audits"
    assert json.loads(str(observed["body"])) == {
        "agent_id": "agent-alpha",
        "auditor_name": "rule_agent_security",
    }


def test_rules_bundle_includes_configured_app_prefix_and_module_sources():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, headers, raw = _raw_request("GET", preview.url, "/static/bundles/rules-page.js")

    bundle = raw.decode("utf-8")
    assert status == 200
    assert headers.get("cache-control") == ["public, max-age=60, must-revalidate"]
    assert "last-modified" in headers
    assert 'window.AgentGuardConfig = {"apiBase": "http://127.0.0.1:38080"};' in bundle
    assert '/* common/app.js */' in bundle
    assert '/* pages/rules/rules.js */' in bundle
    assert 'function resolveRuleModule(globalName, requirePath)' in bundle


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


def test_user_proxy_forwards_set_cookie_and_cookie_header():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["post_path"] = self.path
            body = json.dumps({"user": {"id": 1, "username": "alice"}}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", "agentguard_user_session=session-1; HttpOnly; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            observed["get_path"] = self.path
            observed["cookie"] = self.headers.get("Cookie")
            body = json.dumps({"user": {"id": 1, "username": "alice"}}).encode("utf-8")
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
                status, headers, _ = _raw_request(
                    "POST",
                    preview.url,
                    "/api/user/login",
                    {"username": "alice", "password": "correct horse"},
                )
                assert status == 200
                cookie = headers["set-cookie"][0]
                status, _, _ = _raw_request(
                    "GET",
                    preview.url,
                    "/api/user/me",
                    headers={"Cookie": cookie.split(";", 1)[0]},
                )

    assert status == 200
    assert observed["post_path"] == "/v1/user/login"
    assert observed["get_path"] == "/v1/user/me"
    assert observed["cookie"] == "agentguard_user_session=session-1"


def test_user_password_proxy_forwards_cookie_and_payload():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["cookie"] = self.headers.get("Cookie")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"status": "ok"}).encode("utf-8")
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
                status, _, raw = _raw_request(
                    "POST",
                    preview.url,
                    "/api/user/password",
                    {
                        "current_password": "correct horse",
                        "new_password": "better horse",
                    },
                    headers={"Cookie": "agentguard_user_session=session-1"},
                )

    assert status == 200
    assert json.loads(raw.decode("utf-8")) == {"status": "ok"}
    assert observed["path"] == "/v1/user/password"
    assert observed["cookie"] == "agentguard_user_session=session-1"
    assert json.loads(str(observed["body"])) == {
        "current_password": "correct horse",
        "new_password": "better horse",
    }


def test_user_register_email_code_proxy_forwards_payload():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"status": "ok"}).encode("utf-8")
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
                    "POST",
                    preview.url,
                    "/api/user/register/email-code",
                    {"email": "alice@example.com"},
                )

    assert status == 200
    assert payload == {"status": "ok"}
    assert observed["path"] == "/v1/user/register/email-code"
    assert json.loads(str(observed["body"])) == {"email": "alice@example.com"}


def test_user_external_account_proxy_forwards_requests():
    observed: dict[str, object] = {"post_paths": [], "post_bodies": []}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["post_paths"].append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            observed["post_bodies"].append(self.rfile.read(length).decode("utf-8"))
            body = json.dumps(
                {"external_account": {"id": 7, "provider": "dify", "account_email": "alice@example.com"}}
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            observed["get_path"] = self.path
            body = json.dumps({"external_accounts": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:
            observed["delete_path"] = self.path
            body = json.dumps({"status": "ok"}).encode("utf-8")
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
                post_status, _ = _json_request(
                    "POST",
                    preview.url,
                    "/api/user/dify/bind",
                    {"email": "alice@example.com"},
                )
                generic_post_status, _ = _json_request(
                    "POST",
                    preview.url,
                    "/api/user/external-accounts",
                    {"provider": "n8n", "email": "owner@example.com"},
                )
                get_status, _ = _json_request(
                    "GET",
                    preview.url,
                    "/api/user/external-accounts",
                )
                delete_status, _ = _json_request(
                    "DELETE",
                    preview.url,
                    "/api/user/external-accounts/7",
                )

    assert post_status == 200
    assert generic_post_status == 200
    assert get_status == 200
    assert delete_status == 200
    assert observed["post_paths"] == ["/v1/user/dify/bind", "/v1/user/external-accounts"]
    first_body, second_body = [json.loads(body) for body in observed["post_bodies"]]
    assert first_body["email"] == "alice@example.com"
    assert second_body == {"provider": "n8n", "email": "owner@example.com"}
    assert observed["get_path"] == "/v1/user/external-accounts"
    assert observed["delete_path"] == "/v1/user/external-accounts/7"


def test_user_organization_group_invitation_proxy_forwards_requests():
    observed: dict[str, list[str]] = {"get": [], "post": [], "patch": [], "delete": []}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["get"].append(self.path)
            body = json.dumps({}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            observed["post"].append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PATCH(self) -> None:
            observed["patch"].append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:
            observed["delete"].append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({"status": "ok"}).encode("utf-8")
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
                for path in [
                    "/api/user/organizations?limit=10",
                    "/api/user/organizations/1",
                    "/api/user/organizations/1/groups",
                    "/api/user/organizations/1/members",
                    "/api/user/groups?organization_id=1",
                    "/api/user/groups/2",
                    "/api/user/groups/2/members",
                ]:
                    assert _json_request("GET", preview.url, path)[0] == 200
                for path in [
                    "/api/user/organizations",
                    "/api/user/organizations/1/groups",
                    "/api/user/groups/2/invitations",
                    "/api/user/invitations/accept",
                ]:
                    assert _json_request("POST", preview.url, path, {"name": "demo"})[0] == 200
                for path in [
                    "/api/user/me",
                    "/api/user/organizations/1",
                    "/api/user/organizations/1/members/7",
                    "/api/user/groups/2",
                    "/api/user/groups/2/members/7",
                ]:
                    assert _json_request("PATCH", preview.url, path, {"display_name": "Demo"})[0] == 200
                assert _json_request("DELETE", preview.url, "/api/user/organizations/1")[0] == 200
                assert _json_request("DELETE", preview.url, "/api/user/organizations/1/members/7")[0] == 200
                assert _json_request("DELETE", preview.url, "/api/user/groups/2")[0] == 200
                assert _json_request("DELETE", preview.url, "/api/user/groups/2/members/7")[0] == 200

    assert observed["get"] == [
        "/v1/user/organizations?limit=10",
        "/v1/user/organizations/1",
        "/v1/user/organizations/1/groups",
        "/v1/user/organizations/1/members",
        "/v1/user/groups?organization_id=1",
        "/v1/user/groups/2",
        "/v1/user/groups/2/members",
    ]
    assert observed["post"] == [
        "/v1/user/organizations",
        "/v1/user/organizations/1/groups",
        "/v1/user/groups/2/invitations",
        "/v1/user/invitations/accept",
    ]
    assert observed["patch"] == [
        "/v1/user/me",
        "/v1/user/organizations/1",
        "/v1/user/organizations/1/members/7",
        "/v1/user/groups/2",
        "/v1/user/groups/2/members/7",
    ]
    assert observed["delete"] == [
        "/v1/user/organizations/1",
        "/v1/user/organizations/1/members/7",
        "/v1/user/groups/2",
        "/v1/user/groups/2/members/7",
    ]


def test_user_openclaw_binding_proxy_forwards_requests():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["get_path"] = self.path
            body = json.dumps({"openclaw_bindings": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:
            observed["delete_path"] = self.path
            body = json.dumps(
                {"status": "ok", "provider": "openclaw", "unbound_count": 3, "agent_ids": ["ag_1", "ag_2", "ag_3"]}
            ).encode("utf-8")
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
                get_status, get_payload = _json_request(
                    "GET",
                    preview.url,
                    "/api/user/openclaw-bindings",
                )
                delete_status, delete_payload = _json_request(
                    "DELETE",
                    preview.url,
                    "/api/user/openclaw-bindings",
                )

    assert get_status == 200
    assert get_payload == {"openclaw_bindings": []}
    assert delete_status == 200
    assert delete_payload == {
        "status": "ok",
        "provider": "openclaw",
        "unbound_count": 3,
        "agent_ids": ["ag_1", "ag_2", "ag_3"],
    }
    assert observed["get_path"] == "/v1/user/openclaw-bindings"
    assert observed["delete_path"] == "/v1/user/openclaw-bindings"


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


def test_agent_location_tag_patch_proxy_forwards_request():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_PATCH(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "agent": {"agent_id": "agent-a", "location_tag": "domestic"}}).encode("utf-8")
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
                    "/api/agents/agent-a/location-tag",
                    {"location_tag": "domestic"},
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/location-tag"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"]))["location_tag"] == "domestic"


def test_skills_proxy_lists_global_skills():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            body = json.dumps([
                {
                    "owner_agent_id": "agent-a",
                    "skill_unique_id": "agent-a:skill-one",
                    "name": "skill-one",
                    "description": "demo",
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
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request("GET", preview.url, "/api/skills")

    assert status == 200
    assert payload[0]["name"] == "skill-one"
    assert observed["path"] == "/v1/backend/skills"
    assert observed["api_key"] == "test-secret"


def test_agent_skills_proxy_lists_scoped_skills():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            body = json.dumps([
                {
                    "owner_agent_id": "agent-a",
                    "skill_unique_id": "agent-a:skill-one",
                    "name": "skill-one",
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
                status, payload = _json_request("GET", preview.url, "/api/agents/agent-a/skills")

    assert status == 200
    assert payload[0]["skill_unique_id"] == "agent-a:skill-one"
    assert observed["path"] == "/v1/backend/agents/agent-a/skills"


def test_agent_skill_detect_proxy_forwards_payload_and_api_key():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "agent_id": "agent-a", "detected": 1, "results": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    request_body = {
        "skill_unique_ids": ["agent-a:skill-one"],
        "use_llm": True,
        "llm_config": None,
        "llm_concurrency": 4,
    }

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/agents/agent-a/skills/detect",
                    request_body,
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/skills/detect"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"])) == request_body


def test_mcps_proxy_lists_global_mcps():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            body = json.dumps([
                {
                    "owner_agent_id": "agent-a",
                    "mcp_unique_id": "agent-a:mcp-one",
                    "name": "mcp-one",
                    "description": "demo mcp",
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
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request("GET", preview.url, "/api/mcps")

    assert status == 200
    assert payload[0]["name"] == "mcp-one"
    assert observed["path"] == "/v1/backend/mcps"
    assert observed["api_key"] == "test-secret"


def test_agent_mcps_proxy_lists_scoped_mcps():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed["path"] = self.path
            body = json.dumps([
                {
                    "owner_agent_id": "agent-a",
                    "mcp_unique_id": "agent-a:mcp-one",
                    "name": "mcp-one",
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
                status, payload = _json_request("GET", preview.url, "/api/agents/agent-a/mcps")

    assert status == 200
    assert payload[0]["mcp_unique_id"] == "agent-a:mcp-one"
    assert observed["path"] == "/v1/backend/agents/agent-a/mcps"


def test_agent_mcp_detect_proxy_forwards_payload_and_api_key():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            observed["api_key"] = self.headers.get("X-Api-Key")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = self.rfile.read(length).decode("utf-8")
            body = json.dumps({"ok": True, "agent_id": "agent-a", "detected": 1, "results": []}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    request_body = {
        "mcp_unique_ids": ["agent-a:mcp-one"],
        "llm_config": None,
    }

    with _ThreadedServer(UpstreamHandler) as upstream:
        with patched_proxy_target(upstream.url, api_key="test-secret"):
            with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
                status, payload = _json_request(
                    "POST",
                    preview.url,
                    "/api/agents/agent-a/mcps/detect",
                    request_body,
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/agent-a/mcps/detect"
    assert observed["api_key"] == "test-secret"
    assert json.loads(str(observed["body"])) == request_body


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


def test_runtime_session_close_proxy_forwards_request():
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["path"] = self.path
            body = json.dumps({"ok": True, "session": {"session_id": "ags_1", "status": "closed"}}).encode("utf-8")
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
                    "POST",
                    preview.url,
                    "/api/agents/ag_1/runtime/sessions/ags_1/close",
                )

    assert status == 200
    assert payload["ok"] is True
    assert observed["path"] == "/v1/backend/agents/ag_1/runtime/sessions/ags_1/close"


def test_runtime_page_renders_shared_sidebar_and_active_nav():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/runtime.html")

    assert status == 200
    assert 'id="app-sidebar"' in body
    assert 'href="/home.html">Home</a>' in body
    assert 'href="/agents.html">Agents</a>' in body
    assert 'href="/plugins.html"' in body
    assert 'href="/user.html">User Centre</a>' in body
    assert 'href="/user.html#profile"' in body
    assert 'href="/user.html#organizations"' in body
    assert 'href="/user.html#groups"' in body
    assert 'href="/user.html#invitations"' not in body
    assert 'data-user-section="users"' not in body
    assert 'href="/runtime.html"' in body
    assert "active" in body
    assert 'href="/labels.html"' in body
    assert 'data-agent-required="true"' in body
    assert 'id="runtime-audit-detail"' in body
    assert 'id="runtime-audit-arguments"' not in body
    assert 'id="runtime-audit-result"' not in body
    assert "Runtime Sessions" in body
    assert 'id="runtime-session-body"' in body


def test_login_page_is_root_entrypoint():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/")

    assert status == 200
    assert "Sign in to AgentGuard" in body
    assert 'id="auth-panel"' in body
    assert 'id="login-form"' in body
    assert 'id="login-password-toggle"' in body
    assert 'id="register-confirm-password"' in body
    assert 'id="register-password-toggle"' in body
    assert 'id="register-confirm-password-toggle"' in body
    assert 'src="/static/common/i18n.js"' in body
    assert 'id="locale-toggle-button"' in body
    assert "Control Plane" not in body
    assert 'id="app-sidebar"' not in body


def test_home_page_renders_chinese_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/home.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'lang="zh-CN"' in body
    assert "AgentGuard 首页" in body
    assert "首页" in body
    assert "让你的智能体工作流变得可控。" in body


def test_rules_page_renders_chinese_explicit_i18n_bindings_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/rules.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert "当你使用 <strong>Tool Trace</strong> 时" in body
    assert 'aria-label="添加路径段"' in body
    assert 'title="添加路径段"' in body
    assert 'placeholder="该规则对应的 LLM 审查系统提示词。"' in body
    assert 'id="agentguard-page-context-title">规则构建器</span>' in body
    assert 'id="agentguard-page-context-description">从结构化输入构建规则、预览 DSL 输出，并管理未发布与已发布状态。</span>' in body
    assert 'id="sidebar-current-user" hidden' in body



def test_skills_page_renders_chinese_page_context_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/skills.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'aria-label="Skill 概览"' in body
    assert 'title="刷新 Skill 目录"' in body
    assert 'id="agentguard-page-context-title">Skill 安全检测</span>' in body
    assert 'id="agentguard-page-context-description">查看已上报的 Skill，并为当前智能体运行静态检测。</span>' in body



def test_agents_page_renders_chinese_template_copy_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/agents.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'id="agentguard-copy-no-tools-registered">尚未注册任何工具。</span>' in body
    assert 'id="agentguard-copy-no-skills-registered">尚未注册任何 Skill。</span>' in body
    assert 'id="agentguard-copy-delete">删除</span>' in body
    assert 'id="agentguard-copy-only-langchain-delete">此页面仅支持删除 LangChain 和 Dify 智能体记录。</span>' in body



def test_labels_page_renders_chinese_template_copy_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/labels.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'id="agentguard-copy-select-tool">选择一个工具</span>' in body
    assert 'id="agentguard-copy-labels-to-write-for">{tool} 待写入的标签：</span>' in body
    assert 'id="agentguard-copy-remove">移除</span>' in body



def test_plugins_page_renders_chinese_template_copy_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/plugins.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'id="agentguard-copy-no-agent-server-plugins">请先选择一个智能体以查看服务端插件。</span>' in body
    assert 'id="agentguard-copy-switch-on">开启</span>' in body
    assert 'id="agentguard-copy-switch-off">关闭</span>' in body



def test_user_page_renders_chinese_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/user.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert "用户工作台" in body
    assert "当前用户" in body
    assert "生成凭证" in body
    assert "退出登录" in body
    assert "修改密码" in body
    assert "当前密码" in body
    assert "确认新密码" in body
    assert "外部账号" in body
    assert "身份" in body
    assert "绑定 Dify" in body
    assert "绑定 n8n" in body
    assert "提供方" in body
    assert "复制凭证" in body


def test_user_page_profile_uses_table_and_edit_panel():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/user.html")

    assert status == 200
    assert 'id="profile-table-body"' in body
    assert 'data-profile-edit="true"' in body
    assert 'id="profile-edit-panel" hidden' in body
    assert 'id="profile-edit-username"' in body
    assert 'id="profile-edit-email"' in body
    assert ".profile-edit-shell[hidden]" in body
    assert "table-layout: fixed;" in body
    assert ".profile-table-card th," in body
    assert "text-align: left;" in body
    assert 'data-view-panel="users"' not in body


def test_user_page_group_forms_use_group_schema_fields():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/user.html")

    assert status == 200
    assert 'id="group-add-button"' in body
    assert 'id="group-join-button"' in body
    assert 'id="group-create-form" hidden' in body
    assert 'id="group-organization" required' in body
    assert 'id="group-name"' in body
    assert 'id="group-description"' in body
    assert 'id="selected-group-name"' in body
    assert 'id="group-display-name"' not in body
    assert 'id="selected-group-display-name"' not in body
    assert 'class="group-table"' in body
    assert 'data-group-invite="' in body
    assert 'data-group-edit="' in body
    assert 'data-group-delete="' in body
    assert 'class="table-action-buttons"' in body
    assert 'class="group-members-row"' in body
    assert "<h3>Members</h3>" not in body
    assert "groupCreateOpen" in body
    assert 'data-view-panel="invitations"' not in body
    assert 'id="invitation-create-form"' not in body
    assert 'id="invitation-accept-form"' not in body
    assert 'id="join-modal-backdrop" hidden' in body
    assert 'id="join-group-form"' in body
    assert 'id="join-group-token"' in body
    assert 'id="submit-join-modal-button"' in body
    assert 'data-i18n="Join">Join</button>' in body
    assert "openJoinModal()" in body
    assert "window.prompt(" not in body
    assert ".compact-form[hidden]" in body
    assert "group_name:" in body
    assert "group_description:" in body


def test_user_page_organization_forms_use_organization_schema_fields():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/user.html")

    assert status == 200
    assert 'id="organization-add-button"' in body
    assert 'id="organization-create-form" hidden' in body
    assert 'id="organization-name"' in body
    assert 'id="organization-description"' in body
    assert 'id="selected-organization-name"' in body
    assert 'id="organization-display-name"' not in body
    assert 'id="selected-organization-display-name"' not in body
    assert 'class="organization-table"' in body
    assert 'data-organization-edit="' in body
    assert 'data-organization-delete="' in body
    assert 'class="organization-members-row"' in body
    assert "organizationCreateOpen" in body
    assert "organization_name:" in body
    assert "organization_description:" in body


def test_user_page_uses_ticket_style_confirm_modal_for_group_and_organization_delete():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/user.html")

    assert status == 200
    assert 'id="confirm-modal-backdrop" hidden' in body
    assert 'class="ticket-modal confirm-modal"' in body
    assert 'id="confirm-modal-title">Confirm Delete</h3>' in body
    assert 'id="confirm-modal-button" type="button">Delete</button>' in body
    assert 'openConfirmModal({' in body
    assert 'title: t("Delete Organization")' in body
    assert 'title: t("Delete Group")' in body
    assert "window.confirm(" not in body


def test_runtime_page_renders_chinese_template_copy_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/runtime.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'id="agentguard-copy-runtime-unreachable">不可达</span>' in body
    assert 'id="agentguard-copy-runtime-empty-sessions">该智能体尚未创建任何运行时会话。</span>' in body
    assert 'id="agentguard-copy-runtime-close">关闭</span>' in body



def test_rules_page_renders_chinese_template_copy_when_language_cookie_set():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, _headers, raw = _raw_request(
            "GET",
            preview.url,
            "/rules.html",
            headers={"Cookie": "agentguard.language=zh"},
        )

    body = raw.decode("utf-8")
    assert status == 200
    assert 'data-agentguard-server-language="zh"' in body
    assert 'id="agentguard-copy-rules-guided-builder">引导式规则构建器</span>' in body
    assert 'id="agentguard-copy-rules-edit-path">编辑路径</span>' in body
    assert 'id="agentguard-copy-rules-trace-empty">TRACE 为空。点击 + 添加第一个段。</span>' in body


def test_home_page_renders_intro_and_home_active_nav():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/home.html")

    assert status == 200
    assert "AgentGuard Home" in body
    assert "AgentGuard" in body
    assert "keeps your agent workflow in control." in body
    assert "DashBoard" in body
    assert 'href="/agents.html"' in body
    assert 'href="/plugins.html"' in body
    assert '<a class="sidebar-nav-item active" href="/home.html">Home</a>' in body
    assert 'href="/labels.html"' in body
    assert 'data-agent-required="true"' in body


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


def test_skills_page_renders_skill_workspace():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/skills.html")

    assert status == 200
    assert "Registered Skills" in body
    assert "Detect selected" in body
    assert 'href="/skills.html"' in body
    assert 'data-agent-required="true"' in body


def test_mcps_page_renders_mcp_workspace():
    with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
        status, body = _text_request("GET", preview.url, "/mcps.html")

    assert status == 200
    assert "Registered MCP Services" in body
    assert "Detect selected" in body
    assert 'href="/mcps.html"' in body
    assert 'data-agent-required="true"' in body


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


def test_mock_mode_lists_and_detects_agent_skills():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            status, skills = _json_request("GET", preview.url, "/api/skills")
            scoped_status, scoped_skills = _json_request("GET", preview.url, "/api/agents/agent-beta/skills")
            skill_id = scoped_skills[0]["skill_unique_id"]
            detect_status, detect_payload = _json_request(
                "POST",
                preview.url,
                "/api/agents/agent-beta/skills/detect",
                {"skill_unique_ids": [skill_id], "use_llm": True},
            )
            after_status, after_skills = _json_request("GET", preview.url, "/api/agents/agent-beta/skills")

    assert status == 200
    assert any(item["name"] == "customer_email_skill" for item in skills)
    assert scoped_status == 200
    assert [item["name"] for item in scoped_skills] == ["credential_exfiltration_skill"]
    assert detect_status == 200
    assert detect_payload["ok"] is True
    assert detect_payload["results"][0]["detect_result"]["label"] == "malicious"
    assert after_status == 200
    assert after_skills[0]["detect_result"]["label"] == "malicious"
    assert after_skills[0]["detect_result"]["metadata"]["llm_review"]["skipped"] is False


def test_mock_mode_lists_and_detects_agent_mcps():
    with patched_mock_mode():
        with _ThreadedServer(frontend_app.FrontendPreviewHandler) as preview:
            status, mcps = _json_request("GET", preview.url, "/api/mcps")
            scoped_status, scoped_mcps = _json_request("GET", preview.url, "/api/agents/agent-alpha/mcps")
            mcp_id = scoped_mcps[0]["mcp_unique_id"]
            detect_status, detect_payload = _json_request(
                "POST",
                preview.url,
                "/api/agents/agent-alpha/mcps/detect",
                {"mcp_unique_ids": [mcp_id]},
            )
            after_status, after_mcps = _json_request("GET", preview.url, "/api/agents/agent-alpha/mcps")

    assert status == 200
    assert any(item["name"] == "local_mcp" for item in mcps)
    assert scoped_status == 200
    assert [item["name"] for item in scoped_mcps] == ["local_mcp"]
    assert detect_status == 200
    assert detect_payload["ok"] is True
    assert detect_payload["results"][0]["detect_result"]["label"] == "benign"
    assert after_status == 200
    assert after_mcps[0]["detect_result"]["label"] == "benign"


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
                {"source": 'RULE: sample\nPHASES: tool_before\nTRACE: A -> B\nCONDITION: A.name == "shell.exec"\nPOLICY: DENY'},
            )
            bad_status, bad_payload = _json_request(
                "POST",
                preview.url,
                "/api/rules/check",
                {"source": "RULE: broken\nPHASES: tool_before\nTRACE: A -> B\nPOLICY: DENY"},
            )
            phase_only_status, phase_only_payload = _json_request(
                "POST",
                preview.url,
                "/api/rules/check",
                {"source": "RULE: phase_only\nPHASES: tool_before\nCONDITION: *\nPOLICY: DENY"},
            )

    assert ok_status == 200
    assert ok_payload["ok"] is True
    assert ok_payload["rule_count"] == 1
    assert isinstance(ok_payload["warnings"], list)
    assert bad_status == 200
    assert bad_payload["ok"] is False
    assert bad_payload["errors"]
    assert phase_only_status == 200
    assert phase_only_payload["ok"] is True
    assert any("no ON/TRACE match" in item["message"] for item in phase_only_payload["warnings"])


def test_mock_mode_reload_updates_published_rules():
    source = "\n\n".join([
        "\n".join([
            "RULE: alpha_email_guard",
            "PHASES: tool_before",
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
            "PHASES: tool_before",
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
                {"source": 'RULE: alpha_agent_only\nPHASES: tool_before\nTRACE: A -> B\nCONDITION: A.name == "shell.exec"\nPOLICY: DENY'},
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
