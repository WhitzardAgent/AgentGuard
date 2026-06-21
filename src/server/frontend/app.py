from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import mimetypes
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

# The mock backend is an optional offline-preview helper. Production deployments
# proxy to a real AgentGuard server and do not require it.
try:
    from frontend.mock_backend import MOCK_BACKEND
except ModuleNotFoundError:
    try:
        from mock_backend import MOCK_BACKEND
    except ModuleNotFoundError:
        MOCK_BACKEND = None


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
PARTIALS_DIR = TEMPLATES_DIR / "partials"
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = BASE_DIR / "assets"
API_BASE_URL = os.environ.get("AGENTGUARD_API_BASE", "http://127.0.0.1:38080").rstrip("/")
BACKEND_API_PREFIX = "v1/backend"
API_KEY = os.environ.get("AGENTGUARD_API_KEY", "").strip()
USE_MOCK_BACKEND = os.environ.get("AGENTGUARD_USE_MOCK", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

PAGE_ROUTES = {
    "/": "home.html",
    "/index.html": "home.html",
    "/agents": "agents.html",
    "/agents.html": "agents.html",
    "/plugins": "plugins.html",
    "/plugins.html": "plugins.html",
    "/user": "user.html",
    "/user.html": "user.html",
    "/labels": "labels.html",
    "/labels.html": "labels.html",
    "/rules": "rules.html",
    "/rules.html": "rules.html",
    "/runtime": "runtime.html",
    "/runtime.html": "runtime.html",
}

PAGE_TAB_KEYS = {
    "home.html": "home",
    "agents.html": "agents",
    "plugins.html": "plugins",
    "user.html": "user",
    "labels.html": "labels",
    "rules.html": "rules",
    "runtime.html": "runtime",
}

SIDEBAR_TABS = ("home", "agents", "plugins", "user", "labels", "rules", "runtime")


class FrontendPreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("GET", path, query):
            return

        if path == "/api/tools":
            self._proxy("tools", method="GET", query=query)
            return

        if path == "/api/rules":
            self._proxy("rules", method="GET", query=query)
            return

        if path == "/api/health":
            self._proxy("health", method="GET", query=query)
            return

        if path == "/api/stats":
            self._proxy("stats", method="GET", query=query)
            return

        if path == "/api/traffic":
            self._proxy("traffic", method="GET", query=query)
            return

        if path == "/api/audit/recent":
            self._proxy("audit/recent", method="GET", query=query)
            return

        if path == "/api/approvals":
            self._proxy("approvals", method="GET", query=query)
            return

        if path.startswith("/api/agents/") and "/runtime/" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/plugins/config"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/plugins/available"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/rules"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and "/tools" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/assets/"):
            self._serve_asset(path)
            return

        if path.startswith("/static/"):
            self._serve_static(path)
            return

        page_name = PAGE_ROUTES.get(path)
        if page_name is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        self._serve_template(page_name)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("POST", path, query):
            return

        if path == "/api/rules/check":
            self._proxy("rules/check", method="POST", query=query)
            return

        if path == "/api/rules/reload":
            self._proxy("rules/reload", method="POST", query=query)
            return

        if path == "/api/plugins/config":
            self._proxy("plugins/config", method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/plugins/config"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/rules"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/approvals/") and (
            path.endswith("/approve") or path.endswith("/deny")
        ):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("DELETE", path, query):
            return

        if path.startswith("/api/agents/") and "/rules/" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="DELETE", query=query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("PATCH", path, query):
            return

        if path.startswith("/api/agents/") and path.endswith("/labels"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="PATCH", query=query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def _maybe_handle_mock(self, method: str, path: str, query: str) -> bool:
        if not USE_MOCK_BACKEND or MOCK_BACKEND is None:
            return False
        if not path.startswith("/api/"):
            return False
        return MOCK_BACKEND.try_handle(self, method=method, path=path, query=query)

    def _serve_static(self, request_path: str) -> None:
        relative_path = request_path.removeprefix("/static/")
        file_path = (STATIC_DIR / relative_path).resolve()

        if not self._is_safe_path(file_path, STATIC_DIR):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        if relative_path == "common/app.js":
            prefix = (
                f"window.AgentGuardConfig = "
                f"{json.dumps({'apiBase': API_BASE_URL}, ensure_ascii=False)};\n"
            ).encode("utf-8")
            body = prefix + file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        mime_type, _ = mimetypes.guess_type(file_path.name)
        self._serve_file(file_path, mime_type or "application/octet-stream")

    def _serve_asset(self, request_path: str) -> None:
        relative_path = request_path.removeprefix("/assets/")
        file_path = (ASSETS_DIR / relative_path).resolve()

        if not self._is_safe_path(file_path, ASSETS_DIR):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        mime_type, _ = mimetypes.guess_type(file_path.name)
        self._serve_file(file_path, mime_type or "application/octet-stream")

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_template(self, page_name: str) -> None:
        path = TEMPLATES_DIR / page_name
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        body = self._render_template(page_name).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _render_template(self, page_name: str) -> str:
        content = (TEMPLATES_DIR / page_name).read_text(encoding="utf-8")
        tab_key = PAGE_TAB_KEYS.get(page_name, "")
        replacements = {
            "{{ shared:sidebar }}": self._render_sidebar(tab_key),
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        return content

    @staticmethod
    def _render_sidebar(active_tab: str) -> str:
        content = (PARTIALS_DIR / "sidebar.html").read_text(encoding="utf-8")
        for tab_name in SIDEBAR_TABS:
            active_class = " active" if tab_name == active_tab else ""
            content = content.replace(f"{{{{ {tab_name}_active }}}}", active_class)
        return content

    def _proxy(self, upstream_path: str, *, method: str, query: str = "") -> None:
        target_url = urljoin(f"{API_BASE_URL}/", self._backend_upstream_path(upstream_path))
        if query:
            target_url = f"{target_url}?{query}"
        body = self._read_request_body() if method in ("POST", "PUT", "PATCH", "DELETE") else None
        headers = {
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = self.headers.get(
                "Content-Type", "application/json; charset=utf-8"
            )
        if API_KEY:
            headers["X-Api-Key"] = API_KEY

        request = Request(target_url, data=body, headers=headers, method=method)

        try:
            with urlopen(request, timeout=5) as response:
                upstream_body = response.read()
                content_type = response.headers.get(
                    "Content-Type", "application/json; charset=utf-8"
                )
        except HTTPError as exc:
            upstream_message = self._read_http_error(exc)
            self._send_json(
                {"ok": False, "error": upstream_message or f"upstream returned {exc.code}"},
                status=HTTPStatus.BAD_GATEWAY,
            )
            return
        except URLError as exc:
            self._send_json(
                {"ok": False, "error": f"cannot reach AgentGuard API: {exc.reason}"},
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(upstream_body)))
        self.end_headers()
        self.wfile.write(upstream_body)

    @staticmethod
    def _backend_upstream_path(upstream_path: str) -> str:
        normalized = upstream_path.strip("/")
        if normalized.startswith("v1/"):
            return normalized
        return f"{BACKEND_API_PREFIX}/{normalized}"

    def _read_request_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return None
        try:
            length = int(raw_length)
        except ValueError:
            return None
        if length <= 0:
            return None
        return self.rfile.read(length)

    @staticmethod
    def _read_http_error(exc: HTTPError) -> str:
        try:
            body = exc.read()
        except Exception:
            return ""
        if not body:
            return ""
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except Exception:
            return body.decode("utf-8", errors="replace").strip()
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error")
            if detail:
                return str(detail)
        return str(payload)

    def _send_json(self, payload: dict[str, object], *, status: HTTPStatus) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _is_safe_path(candidate: Path, parent: Path) -> bool:
        try:
            candidate.relative_to(parent.resolve())
        except ValueError:
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str | None = None, port: int | None = None) -> None:
    h = host or os.environ.get("FRONTEND_HOST", "127.0.0.1")
    p = port or int(os.environ.get("FRONTEND_PORT", "8008"))
    server = ThreadingHTTPServer((h, p), FrontendPreviewHandler)
    print(f"AgentGuard frontend  http://{h}:{p}")
    if USE_MOCK_BACKEND:
        print("Mocking agent/tool/rule frontend API routes from frontend.mock_backend")
    else:
        print(f"Proxying /api/tools to {API_BASE_URL}/v1/backend/tools")
        print(f"Proxying /api/rules to {API_BASE_URL}/v1/backend/rules")
        print(f"Proxying /api/rules/reload to {API_BASE_URL}/v1/backend/rules/reload")
        print("Proxying /api/agents/{agent_id}/rules to agent-scoped rule endpoints")
        print("Proxying /api/agents/{agent_id}/plugins/config to agent-scoped plugin endpoints")
        print("Proxying /api/agents/{agent_id}/plugins/available to agent-scoped plugin catalog endpoints")
        print("Proxying /api/agents/{agent_id}/tools/{tool_name}/labels to tool-label patch endpoint")
        print(f"Proxying /api/health to {API_BASE_URL}/v1/backend/health")
        print(f"Proxying /api/stats to {API_BASE_URL}/v1/backend/stats")
        print(f"Proxying /api/traffic to {API_BASE_URL}/v1/backend/traffic")
        print(f"Proxying /api/audit/recent to {API_BASE_URL}/v1/backend/audit/recent")
        print(f"Proxying /api/approvals to {API_BASE_URL}/v1/backend/approvals")
        print(f"Proxying /api/plugins/config to {API_BASE_URL}/v1/backend/plugins/config")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
