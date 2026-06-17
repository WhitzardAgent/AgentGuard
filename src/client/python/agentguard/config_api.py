"""Local HTTP API for updating client runtime configuration."""
from __future__ import annotations

import hashlib
import importlib
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentguard.plugins.registry import registered_plugins
from agentguard.utils.json import safe_dumps, safe_loads

PLUGIN_CONFIG_PATH = "/v1/client/plugins/config"
PLUGIN_LIST_PATH = "/v1/client/plugins/list"
PLUGIN_UPDATE_PATH = "/v1/client/plugins/update"
LEGACY_CHECKER_CONFIG_PATH = "/v1/client/checkers/config"
LEGACY_CHECKER_LIST_PATH = "/v1/client/checkers/list"
LEGACY_CHECKER_UPDATE_PATH = "/v1/client/checkers/update"
CLIENT_HEALTH_PATH = "/v1/client/health"

_EVENT_PHASE = {
    "llm_input": "llm_before",
    "llm_output": "llm_after",
    "tool_invoke": "tool_before",
    "tool_result": "tool_after",
}
_DEPRECATED_PLUGIN_NAMES = {"memory", "llm_thought", "final_response"}
_SAFE_FILENAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.py$")
_CONFIG_PATHS = {PLUGIN_CONFIG_PATH, LEGACY_CHECKER_CONFIG_PATH}
_LIST_PATHS = {PLUGIN_LIST_PATH, LEGACY_CHECKER_LIST_PATH}
_UPDATE_PATHS = {PLUGIN_UPDATE_PATH, LEGACY_CHECKER_UPDATE_PATH}


class ClientConfigAPIServer:
    """Small local-only HTTP API bound to one AgentGuard instance."""

    def __init__(self, guard: Any, *, host: str = "127.0.0.1", port: int = 38181) -> None:
        self.guard = guard
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            return f"http://{self.host}:{self.port}"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def plugin_config_url(self) -> str:
        return f"{self.base_url}{PLUGIN_CONFIG_PATH}"

    @property
    def plugin_list_url(self) -> str:
        return f"{self.base_url}{PLUGIN_LIST_PATH}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}{CLIENT_HEALTH_PATH}"

    def start(self) -> str:
        if self._server is not None:
            return self.plugin_config_url
        handler = self._handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.plugin_config_url

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        guard = self.guard

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = safe_dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _read_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                data = safe_loads(raw, fallback={})
                return data if isinstance(data, dict) else {}

            def _authorized(self) -> bool:
                expected = getattr(guard, "session_key", None)
                provided = self.headers.get("X-AgentGuard-Session-Key")
                if expected and not provided:
                    self._send(401, {"error": "missing client session key"})
                    return False
                if expected and provided != expected:
                    self._send(403, {"error": "invalid client session key"})
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802
                if self.path == CLIENT_HEALTH_PATH:
                    if not self._authorized():
                        return
                    self._send(
                        200,
                        {
                            "status": "ok",
                            "service": "agentguard-client-config",
                            "session_id": guard.context.session_id,
                            "agent_id": guard.context.agent_id,
                            "user_id": guard.context.user_id,
                        },
                    )
                    return
                if self.path in _LIST_PATHS:
                    if not self._authorized():
                        return
                    plugins_by_name = registered_plugins()
                    plugins = [
                        {
                            "name": name,
                            "description": getattr(cls, "description", ""),
                            "event_types": [
                                getattr(event_type, "value", str(event_type))
                                for event_type in getattr(cls, "event_types", [])
                            ],
                        }
                        for name, cls in sorted(plugins_by_name.items())
                        if name not in _DEPRECATED_PLUGIN_NAMES
                    ]
                    self._send(
                        200,
                        {
                            "status": "ok",
                            "plugins": plugins,
                        },
                    )
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path in _CONFIG_PATHS:
                    if not self._authorized():
                        return
                    body = self._read_body()
                    config: Any
                    if "path" in body:
                        config = str(body["path"])
                    else:
                        config = body.get("config", body)
                    try:
                        guard.update_checker_config(config, sync_remote=False)
                    except Exception as exc:
                        self._send(400, {"status": "error", "error": str(exc)})
                        return
                    self._send(
                        200,
                        {
                            "status": "ok",
                            "applies": "next_event",
                            "endpoint": PLUGIN_CONFIG_PATH,
                        },
                    )
                    return
                if self.path in _UPDATE_PATHS:
                    if not self._authorized():
                        return
                    try:
                        payload = _install_plugin_code(self._read_body())
                    except Exception as exc:
                        self._send(400, {"status": "error", "error": str(exc)})
                        return
                    self._send(200, {"status": "ok", **payload})
                    return
                else:
                    self._send(404, {"error": "not found"})
                    return

        return _Handler


def _install_plugin_code(body: dict[str, Any]) -> dict[str, Any]:
    event_type = str(body.get("event_type") or "").strip()
    phase = _EVENT_PHASE.get(event_type)
    if phase is None:
        allowed = ", ".join(sorted(_EVENT_PHASE))
        raise ValueError(f"unsupported event_type: {event_type!r}; expected one of: {allowed}")

    code = body.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("plugin update requires non-empty 'code'")
    if "@register" not in code:
        raise ValueError("plugin code must use @register(name=..., description=...)")

    filename = body.get("filename")
    if filename is None:
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        filename = f"dynamic_{event_type}_{digest}.py"
    filename = str(filename)
    if not _SAFE_FILENAME.match(filename):
        raise ValueError("filename must be a safe Python filename such as my_checker.py")

    plugin_root = Path(__file__).resolve().parent / "plugins"
    phase_dir = plugin_root / phase
    phase_dir.mkdir(parents=True, exist_ok=True)
    target = phase_dir / filename
    target.write_text(code.rstrip() + "\n", encoding="utf-8")

    module_name = f"agentguard.plugins.{phase}.{target.stem}"
    importlib.invalidate_caches()
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    else:
        importlib.import_module(module_name)

    return {
        "event_type": event_type,
        "phase": phase,
        "filename": filename,
        "path": str(target),
        "module": module_name,
        "registered_plugins": sorted(registered_plugins()),
    }
