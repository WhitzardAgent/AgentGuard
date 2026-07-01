"""Stdlib-based dev server for examples and e2e tests (no uvicorn needed)."""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from backend.api.auth import check_backend_api_key
from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager
from backend.runtime.policy.snapshot_builder import snapshot_dict
from backend.skill_service.router import SkillServiceRouter
from shared.schemas.context import RuntimeContext
from shared.utils.json import safe_dumps, safe_loads


class _Handler(BaseHTTPRequestHandler):
    manager: RuntimeManager
    console: ConsoleState
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
        if not self._authorize_backend_api():
            return
        path = self.path.split("?", 1)[0]
        if path == "/v1/backend/health":
            self._send(200, {"status": "ok", "service": "agentguard-dev"})
        elif path == "/v1/server/policy/snapshot":
            if not self._validate_client_session():
                return
            self._send(200, snapshot_dict(self.manager.policy.store))
        elif path.startswith("/v1/server/approvals/"):
            if not self._validate_client_session():
                return
            ticket_id = path.rsplit("/", 1)[-1]
            ticket = self.manager.review_queue.get(ticket_id)
            if ticket is None or not self._ticket_belongs_to_headers(ticket):
                self._send(404, {"error": "ticket not found"})
                return
            wait_ms = int(self._query_params().get("wait_ms", "0") or 0)
            waited = self.manager.review_queue.wait(ticket_id, timeout_s=max(wait_ms, 0) / 1000.0)
            if waited is None:
                self._send(404, {"error": "ticket not found"})
                return
            self._send(200, waited)
        elif path == "/v1/backend/sessions":
            self._send(200, {"sessions": self.manager.session_pool.list()})
        elif path == "/v1/backend/auditors":
            from backend.audit import auditor_descriptions

            self._send(200, {
                "auditors": [
                    {"name": name, "description": description}
                    for name, description in sorted(auditor_descriptions().items())
                ]
            })
        elif path == "/v1/backend/tools":
            self._send(200, self.console.tools())
        elif path == "/v1/backend/skills":
            self._send(200, self.console.skills())
        elif path == "/v1/backend/mcps":
            self._send(200, self.console.mcps())
        elif path == "/v1/backend/approvals":
            self._send(200, self.console.approvals())
        elif path.startswith("/v1/backend/agents/") and path.endswith("/tools"):
            agent_id = path.split("/")[4]
            self._send(200, self.console.tools(agent_id))
        elif path.startswith("/v1/backend/agents/") and path.endswith("/skills"):
            agent_id = path.split("/")[4]
            self._send(200, self.console.skills(agent_id))
        elif path.startswith("/v1/backend/agents/") and path.endswith("/mcps"):
            agent_id = path.split("/")[4]
            self._send(200, self.console.mcps(agent_id))
        elif path.startswith("/v1/backend/agents/") and path.endswith("/runtime/approvals"):
            agent_id = path.split("/")[4]
            self._send(200, self.console.approvals(agent_id))
        elif path.startswith("/v1/backend/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            record = self.manager.session_pool.get(
                session_id,
                agent_id=self._query_params().get("agent_id"),
                user_id=self._query_params().get("user_id"),
            )
            if record is None:
                self._send(404, {"error": f"session not found: {session_id}"})
            else:
                self._send(200, record)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize_backend_api():
            return
        body = self._read_body()
        if self.path == "/v1/server/guard/decide":
            body["_transport"] = self._transport_metadata(enforce_session_key=True)
            try:
                self._send(200, self.manager.decide(body))
            except PermissionError as exc:
                self._send_session_key_error(exc)
        elif self.path == "/v1/server/skills/run":
            if not self._validate_client_session():
                return
            self._send(200, self.skills.run(body))
        elif self.path == "/v1/server/trace/upload":
            body["_transport"] = self._transport_metadata(enforce_session_key=True)
            try:
                count = self.manager.record_uploaded_trace(body)
            except PermissionError as exc:
                self._send_session_key_error(exc)
                return
            else:
                self._send(200, {"status": "received", "entries": count})
        elif self.path == "/v1/server/tools/report":
            if not self._validate_client_session():
                return
            tool = self.console.register_tool(body.get("context") or {}, body.get("tool") or {})
            if tool is None:
                self._send(400, {"error": "agent_id and tool.name are required"})
            else:
                self._send(200, {"status": "ok", "tool": tool})
        elif self.path == "/v1/server/tools/sync":
            if not self._validate_client_session():
                return
            result = self.console.sync_tools(body.get("context") or {}, body.get("tools") or [])
            if result is None:
                self._send(400, {"error": "agent_id is required"})
            else:
                self._send(200, {"status": "ok", **result})
        elif self.path == "/v1/server/skills/report":
            if not self._validate_client_session():
                return
            result = self.console.register_skills(
                body.get("context") or {},
                body.get("skills") or [],
                body.get("scan") or {},
            )
            if result is None:
                self._send(400, {"error": "agent_id is required"})
            else:
                self._send(
                    200,
                    {
                        "status": "ok",
                        "skill_count": result["skill_count"],
                        "skills": result["skills"],
                    },
                )
        elif self.path == "/v1/server/mcps/report":
            if not self._validate_client_session():
                return
            result = self.console.register_mcps(
                body.get("context") or {},
                body.get("mcps") or [],
                body.get("scan") or {},
            )
            if result is None:
                self._send(400, {"error": "agent_id is required"})
            else:
                self._send(
                    200,
                    {
                        "status": "ok",
                        "mcp_count": result["mcp_count"],
                        "mcps": result["mcps"],
                    },
                )
        elif self.path == "/v1/server/session/register":
            context = RuntimeContext.from_dict(body.get("context") or {})
            try:
                record = self.manager.register_client_session(
                    context,
                    client_ip=self.client_address[0],
                    client_key=self.headers.get("X-AgentGuard-Session-Key"),
                    enforce_key=True,
                )
            except PermissionError as exc:
                self._send_session_key_error(exc)
                return
            self._send(200, {"status": "ok", "session": record})
        elif self.path == "/v1/server/session/unregister":
            session_id = self.headers.get("X-AgentGuard-Session-Id")
            if not session_id:
                self._send_session_key_error(PermissionError("missing client session id"))
                return
            try:
                removed = self.manager.session_pool.remove(
                    session_id,
                    agent_id=self.headers.get("X-AgentGuard-Agent-Id"),
                    user_id=self.headers.get("X-AgentGuard-User-Id"),
                    client_key=self.headers.get("X-AgentGuard-Session-Key"),
                    enforce_key=True,
                )
            except PermissionError as exc:
                self._send_session_key_error(exc)
                return
            self._send(200, {"status": "ok", "session_id": session_id, "removed": removed})
        elif self.path.startswith("/v1/backend/approvals/") and self.path.endswith("/approve"):
            ticket_id = self.path.split("/")[4]
            if self.console.resolve_ticket(ticket_id, approved=True, note=body.get("note", "")):
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "ticket not found or already resolved"})
        elif self.path.startswith("/v1/backend/approvals/") and self.path.endswith("/deny"):
            ticket_id = self.path.split("/")[4]
            if self.console.resolve_ticket(ticket_id, approved=False, note=body.get("note", "")):
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "ticket not found or already resolved"})
        elif self.path.startswith("/v1/backend/agents/") and self.path.endswith("/rules/generate"):
            agent_id = self.path.split("/")[4]
            result = self.console.generate_rule(
                agent_id,
                str(body.get("requirement") or ""),
                user_feedback=str(body.get("user_feedback") or ""),
                current_candidate=body.get("current_candidate"),
                max_rounds=int(body.get("max_rounds") or 4),
                llm_config=body.get("llm_config") if isinstance(body.get("llm_config"), dict) else None,
            )
            self._send(int(result.pop("code", 200 if result.get("ok") else 422)), result)
        elif self.path.startswith("/v1/backend/agents/") and self.path.endswith("/skills/detect"):
            agent_id = self.path.split("/")[4]
            skill_unique_ids = body.get("skill_unique_ids") or []
            if not isinstance(skill_unique_ids, list):
                skill_unique_ids = []
            result = self.console.detect_skills(
                agent_id,
                skill_unique_ids,
                use_llm=bool(body.get("use_llm")),
                llm_config=body.get("llm_config") if isinstance(body.get("llm_config"), dict) else None,
            )
            self._send(int(result.pop("code", 200 if result.get("ok") else 422)), result)
        elif self.path.startswith("/v1/backend/agents/") and self.path.endswith("/mcps/detect"):
            agent_id = self.path.split("/")[4]
            mcp_unique_ids = body.get("mcp_unique_ids") or []
            if not isinstance(mcp_unique_ids, list):
                mcp_unique_ids = []
            result = self.console.detect_mcps(
                agent_id,
                mcp_unique_ids,
                llm_config=body.get("llm_config") if isinstance(body.get("llm_config"), dict) else None,
            )
            self._send(int(result.pop("code", 200 if result.get("ok") else 422)), result)
        elif self.path == "/v1/backend/plugins/config":
            try:
                loaded = self.manager.update_plugin_config(body.get("config"))
            except Exception as exc:
                self._send(400, {"status": "error", "error": str(exc)})
                return
            client_config = body.get("client_config") or body.get("config")
            timeout_s = float(body.get("timeout_s", 2.0) or 2.0)
            client_updates = []
            for principal in body.get("client_principals") or []:
                client_updates.extend(
                    self.manager.update_client_plugin_config(
                        principal,
                        client_config,
                        remote_plugin_config=body.get("config"),
                        timeout_s=timeout_s,
                    )
                )
            client_updates.extend(
                [
                    _push_client_plugin_config(
                        url,
                        client_config,
                        timeout_s,
                        client_key=_client_key_for_url(self.manager, url),
                    )
                    for url in body.get("client_config_urls") or []
                ]
            )
            self._send(
                200,
                {
                    "status": "ok",
                    "loaded_plugins": loaded,
                    "client_updates": client_updates,
                },
            )
        elif self.path == "/v1/backend/audit/custom/run":
            session_id = body.get("session_id")
            auditor_name = body.get("auditor_name")
            if not isinstance(session_id, str) or not session_id:
                self._send(400, {"error": "session_id is required"})
                return
            if not isinstance(auditor_name, str) or not auditor_name:
                self._send(400, {"error": "auditor_name is required"})
                return
            agent_id = body.get("agent_id")
            user_id = body.get("user_id")
            trace = self.manager.get_trace_records(
                session_id,
                agent_id=str(agent_id) if agent_id is not None else None,
                user_id=str(user_id) if user_id is not None else None,
            )
            if not trace:
                self._send(
                    404,
                    {
                        "error": (
                            "trace not found for "
                            f"session_id={session_id}, agent_id={agent_id}, user_id={user_id}"
                        )
                    },
                )
                return
            try:
                from backend.audit import auditor_manager

                result = auditor_manager().audit(
                    auditor_name,
                    trace,
                )
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(
                200,
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "auditor_name": auditor_name,
                    "level": result.level,
                    "reason": result.reason,
                    "trace_entries": len(trace),
                    "metadata": result.metadata,
                },
            )
        elif self.path == "/v1/backend/sessions/refresh-stale":
            self._send(200, {"results": self.manager.refresh_stale_sessions()})
        else:
            self._send(404, {"error": "not found"})

    def _transport_metadata(self, *, enforce_session_key: bool) -> dict[str, Any]:
        return {
            "client_ip": self.client_address[0],
            "client_key": self.headers.get("X-AgentGuard-Session-Key"),
            "agent_id": self.headers.get("X-AgentGuard-Agent-Id"),
            "user_id": self.headers.get("X-AgentGuard-User-Id"),
            "enforce_session_key": enforce_session_key,
        }

    def _authorize_backend_api(self) -> bool:
        check = check_backend_api_key(self.path, self.headers.get("X-Api-Key"))
        if check.ok:
            return True
        self._send(check.status_code, {"error": check.error})
        return False

    def _validate_client_session(self) -> bool:
        session_id = self.headers.get("X-AgentGuard-Session-Id")
        if not session_id:
            self._send_session_key_error(PermissionError("missing client session id"))
            return False
        try:
            record = self.manager.session_pool.touch(
                session_id,
                agent_id=self.headers.get("X-AgentGuard-Agent-Id"),
                user_id=self.headers.get("X-AgentGuard-User-Id"),
                client_ip=self.client_address[0],
                client_key=self.headers.get("X-AgentGuard-Session-Key"),
                enforce_key=True,
            )
            if record is None:
                raise PermissionError("unknown client session")
        except PermissionError as exc:
            self._send_session_key_error(exc)
            return False
        return True

    def _send_session_key_error(self, exc: PermissionError) -> None:
        message = str(exc)
        self._send(401 if "missing" in message else 403, {"error": message})

    def _ticket_belongs_to_headers(self, ticket: dict[str, Any]) -> bool:
        principal = dict(ticket.get("principal") or {})
        return (
            str(principal.get("session_id") or "")
            == str(self.headers.get("X-AgentGuard-Session-Id") or "")
            and str(principal.get("agent_id") or "")
            == str(self.headers.get("X-AgentGuard-Agent-Id") or "")
            and str(principal.get("user_id") or "")
            == str(self.headers.get("X-AgentGuard-User-Id") or "")
        )

    def _query_params(self) -> dict[str, str]:
        raw = self.path.split("?", 1)
        if len(raw) == 1:
            return {}
        pairs = [item.split("=", 1) for item in raw[1].split("&") if item]
        return {key: value for key, value in pairs if key}


def start_dev_server(
    port: int = 0,
    *,
    manager: RuntimeManager | None = None,
    console: ConsoleState | None = None,
    skills: SkillServiceRouter | None = None,
) -> tuple[str, ThreadingHTTPServer, threading.Thread]:
    """Start the dev server in a daemon thread. Returns (base_url, server, thread)."""
    bound_manager = manager or RuntimeManager()
    handler = type(
        "BoundHandler",
        (_Handler,),
        {
            "manager": bound_manager,
            "console": console or ConsoleState(bound_manager),
            "skills": skills or SkillServiceRouter(),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return base_url, server, thread


def _push_client_plugin_config(
    url: str,
    config: dict[str, Any],
    timeout_s: float,
    *,
    client_key: str | None = None,
) -> dict[str, Any]:
    body = safe_dumps({"config": config}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if client_key:
        headers["X-AgentGuard-Session-Key"] = client_key
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
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


def _client_key_for_url(manager: RuntimeManager, url: str) -> str | None:
    for session in manager.session_pool.list():
        known_urls = {
            session.get("client_config_url"),
            session.get("client_plugin_list_url"),
            session.get("client_health_url"),
        }
        if url in known_urls:
            key = session.get("client_key")
            return str(key) if key else None
    return None
