"""Stdlib-based dev server for examples and e2e tests (no uvicorn needed)."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentguard.utils.json import safe_dumps, safe_loads
from backend.runtime.manager import RuntimeManager
from backend.runtime.policy.snapshot_builder import snapshot_dict
from backend.skill_service.router import SkillServiceRouter


class _Handler(BaseHTTPRequestHandler):
    manager: RuntimeManager
    skills: SkillServiceRouter

    def log_message(self, *args: Any) -> None:  # silence default logging
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
        return safe_loads(raw, fallback={}) or {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "service": "agentguard-dev"})
        elif self.path == "/v1/policy/snapshot":
            self._send(200, snapshot_dict(self.manager.policy.store))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_body()
        if self.path == "/v1/guard/decide":
            self._send(200, self.manager.decide(body))
        elif self.path == "/v1/skills/run":
            self._send(200, self.skills.run(body))
        elif self.path == "/v1/trace/upload":
            self._send(200, {"status": "received", "entries": len(body.get("entries") or [])})
        else:
            self._send(404, {"error": "not found"})


def start_dev_server(
    port: int = 0,
    *,
    manager: RuntimeManager | None = None,
    skills: SkillServiceRouter | None = None,
) -> tuple[str, ThreadingHTTPServer, threading.Thread]:
    """Start the dev server in a daemon thread. Returns (base_url, server, thread)."""
    handler = type(
        "BoundHandler",
        (_Handler,),
        {"manager": manager or RuntimeManager(), "skills": skills or SkillServiceRouter()},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return base_url, server, thread
