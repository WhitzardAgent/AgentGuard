"""Local HTTP API for updating client runtime configuration."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentguard.utils.json import safe_dumps, safe_loads

CHECKER_CONFIG_PATH = "/v1/client/checkers/config"


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
    def checker_config_url(self) -> str:
        return f"{self.base_url}{CHECKER_CONFIG_PATH}"

    def start(self) -> str:
        if self._server is not None:
            return self.checker_config_url
        handler = self._handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.checker_config_url

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
                self.wfile.write(data)

            def _read_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                data = safe_loads(raw, fallback={})
                return data if isinstance(data, dict) else {}

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._send(200, {"status": "ok", "service": "agentguard-client-config"})
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != CHECKER_CONFIG_PATH:
                    self._send(404, {"error": "not found"})
                    return
                body = self._read_body()
                config: Any
                if "path" in body:
                    config = str(body["path"])
                else:
                    config = body.get("config", body)
                try:
                    guard.update_checker_config(config)
                except Exception as exc:
                    self._send(400, {"status": "error", "error": str(exc)})
                    return
                self._send(
                    200,
                    {
                        "status": "ok",
                        "applies": "next_event",
                        "endpoint": CHECKER_CONFIG_PATH,
                    },
                )

        return _Handler
