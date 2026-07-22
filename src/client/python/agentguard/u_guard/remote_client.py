"""Remote guard client: talk to the server decision service over HTTP."""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.utils.errors import RemoteGuardError
from agentguard.utils.json import safe_dumps, safe_loads


@dataclass
class CircuitBreaker:
    """Simple open/closed breaker based on consecutive failures."""

    threshold: int = 3
    reset_after_s: float = 15.0
    _failures: int = 0
    _opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self.threshold:
            return False
        if (time.time() - self._opened_at) > self.reset_after_s:
            # Half-open: allow a trial request.
            self._failures = self.threshold - 1
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.time()


class RemoteGuardClient:
    def __init__(
        self,
        server_url: str | None,
        *,
        api_key: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        session_key: str | None = None,
        user_ticket: str | None = None,
        timeout_s: float = 5.0,
        retries: int = 2,
        decide_path: str = "/v1/server/guard/decide",
        snapshot_path: str = "/v1/server/policy/snapshot",
        trace_path: str = "/v1/server/trace/upload",
        tool_report_path: str = "/v1/server/tools/report",
        mcp_report_path: str = "/v1/server/mcps/report",
        tool_sync_path: str = "/v1/server/tools/sync",
        approval_path: str = "/v1/server/approvals/{ticket_id}",
        register_path: str = "/v1/server/session/register",
        unregister_path: str = "/v1/server/session/unregister",
        agent_register_path: str = "/v1/server/agents/register",
        agent_sync_path: str = "/v1/server/agents/sync",
        runtime_session_create_path: str = "/v1/server/session/create",
        runtime_session_refresh_path: str = "/v1/server/session/refresh",
        runtime_session_close_path: str = "/v1/server/session/close",
        session_token: str | None = None,
        dpop_proof_factory: Any | None = None,
        use_dpop_auth: bool = False,
        legacy_identity_headers: bool = True,
        approval_wait_timeout_s: float = 600.0,
        approval_wait_chunk_s: float = 25.0,
    ) -> None:
        self.server_url = (server_url or "").rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.agent_id = agent_id
        self.user_id = user_id
        self.session_key = session_key
        self.user_ticket = user_ticket
        self.timeout_s = timeout_s
        self.retries = retries
        self.decide_path = decide_path
        self.snapshot_path = snapshot_path
        self.trace_path = trace_path
        self.tool_report_path = tool_report_path
        self.mcp_report_path = mcp_report_path
        self.tool_sync_path = tool_sync_path
        self.approval_path = approval_path
        self.register_path = register_path
        self.unregister_path = unregister_path
        self.agent_register_path = agent_register_path
        self.agent_sync_path = agent_sync_path
        self.runtime_session_create_path = runtime_session_create_path
        self.runtime_session_refresh_path = runtime_session_refresh_path
        self.runtime_session_close_path = runtime_session_close_path
        self.session_token = session_token
        self.dpop_proof_factory = dpop_proof_factory
        self.use_dpop_auth = use_dpop_auth
        self.legacy_identity_headers = legacy_identity_headers
        self.approval_wait_timeout_s = approval_wait_timeout_s
        self.approval_wait_chunk_s = max(1.0, approval_wait_chunk_s)
        self.breaker = CircuitBreaker()

    @property
    def enabled(self) -> bool:
        return bool(self.server_url)

    def _require_runtime_auth(self, purpose: str) -> None:
        if not self.use_dpop_auth or not self.session_token or not callable(self.dpop_proof_factory):
            raise RemoteGuardError(
                f"{purpose} requires a runtime-auth session_token and DPoP proof factory; create a runtime session first"
            )

    # ---- public API ----------------------------------------------------
    def decide(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        *,
        trajectory_window: list[RuntimeEvent] | None = None,
        local_signals: list[str] | None = None,
        extensions: dict[str, Any] | None = None,
        client_cached_entries: list[dict[str, Any]] | None = None,
    ) -> GuardDecision:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("remote guard decisions")
        if self.breaker.is_open:
            raise RemoteGuardError("circuit breaker open")

        body = {
            "request_id": f"req_{event.event_id}",
            "current_event": event.to_dict(),
            "context": context.to_dict(),
            "trajectory_window": [e.to_dict() for e in (trajectory_window or [])],
            "local_signals": list(local_signals or event.risk_signals),
            "policy_version": context.policy_version,
            "extensions": extensions or {},
            "client_cached_entries": list(client_cached_entries or []),
        }
        payload = self._post(self.decide_path, body)
        decision = payload.get("decision") or {}
        if not decision:
            raise RemoteGuardError("server returned no decision")
        gd = GuardDecision.from_dict(decision)
        for s in payload.get("risk_signals") or []:
            if s not in gd.risk_signals:
                gd.risk_signals.append(s)
        gd.metadata.setdefault("plugin_result", payload.get("plugin_result") or {})
        gd.metadata.setdefault("source", "remote")
        gd = self._await_review_resolution(gd)
        return gd

    def fetch_snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("remote policy snapshot fetches")
        return self._get(self.snapshot_path)

    def upload_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("remote trace uploads")
        return self._post(self.trace_path, trace)

    def report_tool(self, context: RuntimeContext, tool: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("remote tool reports")
        body = {
            "context": context.to_dict(),
            "tool": tool,
        }
        return self._post(self.tool_report_path, body)

    def report_mcps(self, context: RuntimeContext, mcps: list[dict[str, Any]], scan: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("remote MCP reports")
        body = {
            "context": context.to_dict(),
            "mcps": list(mcps),
            "scan": scan if isinstance(scan, dict) else {},
        }
        return self._post(self.mcp_report_path, body)

    def sync_tools(self, context: RuntimeContext, tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("remote tool sync")
        body = {
            "context": context.to_dict(),
            "tools": list(tools),
        }
        return self._post(self.tool_sync_path, body)

    def register_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        return self._post(self.agent_register_path, dict(agent))

    def sync_agents(self, catalog: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        return self._post(self.agent_sync_path, dict(catalog))

    def register_session(self, context: RuntimeContext) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("runtime session sync")
        return self._post(self.register_path, {"context": context.to_dict()})

    def unregister_session(self) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("runtime session cleanup")
        return self._post(self.unregister_path, {})

    def create_runtime_session(
        self,
        body: dict[str, Any],
        *,
        extra_headers_factory: Any | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        return self._post(self.runtime_session_create_path, dict(body), extra_headers_factory=extra_headers_factory)

    def refresh_runtime_session(self) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("runtime session refresh")
        return self._post(self.runtime_session_refresh_path, {})

    def close_runtime_session(self) -> dict[str, Any]:
        if not self.enabled:
            raise RemoteGuardError("no server_url configured")
        self._require_runtime_auth("runtime session close")
        return self._post(self.runtime_session_close_path, {})

    def upload_trace_async(
        self,
        trace: dict[str, Any],
        *,
        on_success: Any | None = None,
        on_error: Any | None = None,
    ) -> threading.Thread | None:
        if not self.enabled:
            return None

        def _worker() -> None:
            try:
                self.upload_trace(trace)
                if callable(on_success):
                    on_success()
            except Exception as exc:  # background sync should not affect agent flow
                if callable(on_error):
                    on_error(exc)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    # ---- transport -----------------------------------------------------
    def _headers(self, *, method: str | None = None, url: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.use_dpop_auth:
            if self.session_token:
                headers["Authorization"] = f"DPoP {self.session_token}"
            elif self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            if callable(self.dpop_proof_factory) and method and url:
                headers["DPoP"] = str(self.dpop_proof_factory(method, url, self.session_token))
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.session_id and self.legacy_identity_headers:
            headers["X-AgentGuard-Session-Id"] = self.session_id
        if self.agent_id and self.legacy_identity_headers:
            headers["X-AgentGuard-Agent-Id"] = self.agent_id
        if self.user_id and self.legacy_identity_headers:
            headers["X-AgentGuard-User-Id"] = self.user_id
        if self.session_key and self.legacy_identity_headers:
            headers["X-AgentGuard-Session-Key"] = self.session_key
        if self.user_ticket:
            headers["X-AgentGuard-User-Ticket"] = self.user_ticket
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None,
        *,
        extra_headers_factory: Any | None = None,
    ) -> dict[str, Any]:
        url = f"{self.server_url}{path}"
        data = safe_dumps(body).encode("utf-8") if body is not None else None
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            headers = self._headers(method=method, url=url)
            if callable(extra_headers_factory):
                headers.update(dict(extra_headers_factory(method, url, body or {}) or {}))
            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                self.breaker.record_success()
                return safe_loads(raw, fallback={}) or {}
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                last_exc = RemoteGuardError(
                    f"remote guard call failed: HTTP {exc.code} {exc.reason}"
                    + (f" body={body}" if body else "")
                )
                if 500 <= int(exc.code) < 600 and attempt < self.retries:
                    time.sleep(min(0.2 * (2**attempt), 1.0))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(min(0.2 * (2**attempt), 1.0))
        self.breaker.record_failure()
        if isinstance(last_exc, RemoteGuardError):
            raise last_exc
        raise RemoteGuardError(f"remote guard call failed: {last_exc}")

    def _post(
        self,
        path: str,
        body: dict,
        *,
        extra_headers_factory: Any | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, body, extra_headers_factory=extra_headers_factory)

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, None)

    def _await_review_resolution(self, decision: GuardDecision) -> GuardDecision:
        if not (decision.requires_user or decision.requires_remote):
            return decision
        ticket_id = str(
            decision.metadata.get("review_ticket_id")
            or decision.metadata.get("ticket_id")
            or ""
        ).strip()
        if not ticket_id:
            return decision

        timeout_s = self.approval_wait_timeout_s
        deadline = None if timeout_s <= 0 else (time.time() + timeout_s)
        max_wait_s = max(self.timeout_s - 0.5, 1.0)
        while True:
            remaining = None if deadline is None else (deadline - time.time())
            if remaining is not None and remaining <= 0:
                return decision
            wait_s = max_wait_s if remaining is None else min(max_wait_s, remaining)
            wait_s = min(wait_s, self.approval_wait_chunk_s)
            path = self.approval_path.format(
                ticket_id=urllib.parse.quote(ticket_id, safe="")
            )
            payload = self._get(f"{path}?wait_ms={int(max(wait_s, 0.0) * 1000)}")
            status = str(payload.get("status") or "").lower()
            if status in {"approved", "denied"}:
                resolved = payload.get("resolved_decision")
                if isinstance(resolved, dict) and resolved.get("decision_type"):
                    return GuardDecision.from_dict(resolved)
                return decision
            if status != "pending":
                return decision
