"""Stdlib-based dev server for examples and e2e tests (no uvicorn needed)."""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from shared.utils.json import safe_dumps, safe_loads
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
            count = self.manager.record_uploaded_trace(body)
            self._send(200, {"status": "received", "entries": count})
        elif self.path == "/v1/checkers/config":
            try:
                loaded = self.manager.update_checker_config(body.get("config"))
            except Exception as exc:
                self._send(400, {"status": "error", "error": str(exc)})
                return
            client_config = body.get("client_config") or body.get("config")
            timeout_s = float(body.get("timeout_s", 2.0) or 2.0)
            client_updates = [
                _push_client_checker_config(url, client_config, timeout_s)
                for url in body.get("client_config_urls") or []
            ]
            self._send(
                200,
                {
                    "status": "ok",
                    "loaded_checkers": loaded,
                    "client_updates": client_updates,
                },
            )
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


def _push_client_checker_config(
    url: str,
    config: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    body = safe_dumps({"config": config}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(timeout_s, 0.1)) as response:
            raw = response.read()
            return {
                "url": url,
                "status": "ok",
                "status_code": response.status,
                "response": safe_loads(raw, fallback={}),
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "status": "error",
            "status_code": exc.code,
            "error": exc.read().decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {"url": url, "status": "error", "error": str(exc)}
