"""Server RuntimeManager: orchestrate a remote guard decision."""
from __future__ import annotations

import copy
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import RuntimeEvent
from backend.audit.audit_logger import AuditLogger
from backend.audit import AuditTraceEntry
from backend.runtime.checkers.base import CheckResult
from backend.runtime.checkers import server_checker_manager
from backend.runtime.checkers.config_utils import merge_checker_configs, normalize_checker_config
from backend.runtime.degrade.planner import DegradePlanner
from backend.runtime.policy.engine import PolicyEngine
from backend.runtime.storage import SessionPool, TraceStore, trace_entry_event_dict
from shared.utils.json import safe_dumps, safe_loads
from shared.utils.time import now_ts


class RuntimeManager:
    """Coordinates server-side checkers, policy, and degradation."""

    def __init__(
        self,
        *,
        policy: PolicyEngine | None = None,
        audit: AuditLogger | None = None,
        checker_config: str | dict[str, Any] | None = None,
        session_health_interval_s: float = 1800.0,
        session_health_max_age_s: float = 0.0,
        enable_session_health_monitor: bool = True,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self.checkers = server_checker_manager(checker_config)
        self.checker_config = checker_config
        self._agent_checker_configs: dict[str, dict[str, dict[str, Any] | None]] = {}
        self._bind_rule_based_checkers()
        self.degrade = DegradePlanner()
        self.audit = audit or AuditLogger()
        self.trace_store = TraceStore()
        self.session_pool = SessionPool()
        self._session_health_interval_s = session_health_interval_s
        self._session_health_max_age_s = session_health_max_age_s
        self._session_health_stop = threading.Event()
        self._session_health_thread: threading.Thread | None = None
        if enable_session_health_monitor:
            self.start_session_health_monitor()
        # Observers receive (event, decision, request) after each decision; used
        # by the console for traffic/telemetry/approval tracking.
        self.observers: list[Callable[[RuntimeEvent, GuardDecision, dict], None]] = []

    def add_observer(
        self, observer: Callable[[RuntimeEvent, GuardDecision, dict], None]
    ) -> None:
        self.observers.append(observer)

    @property
    def policy_version(self) -> str:
        return self.policy.version

    def update_checker_config(self, checker_config: str | dict[str, Any] | None) -> list[str]:
        """Replace server-side checker configuration for subsequent decisions."""
        self.checkers.update_config(checker_config)
        self.checker_config = checker_config
        self._bind_rule_based_checkers()
        return [checker.name for checker in getattr(self.checkers, "checkers", [])]

    def register_client_session(
        self,
        context: RuntimeContext,
        *,
        client_ip: str | None = None,
        client_key: str | None = None,
        enforce_key: bool = False,
        event_dict: dict[str, Any] | None = None,
        timeout_s: float = 2.0,
        push_config: bool = True,
    ) -> dict[str, Any]:
        record = self.session_pool.upsert(
            context,
            client_ip=client_ip or (context.metadata or {}).get("client_ip"),
            client_key=client_key or (context.metadata or {}).get("client_session_key"),
            enforce_key=enforce_key,
            event_dict=event_dict,
        )
        applied = self._apply_agent_checker_config_to_session(record)
        if push_config:
            self._push_agent_checker_config_to_session(applied, timeout_s=timeout_s)
        return applied

    def sessions_for_principal(self, principal: dict[str, Any]) -> list[dict[str, Any]]:
        return self.session_pool.find_by_principal(principal)

    def set_agent_checker_config(
        self,
        agent_id: str,
        checker_config: dict[str, Any],
        *,
        client_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            raise ValueError("agent_id is required")
        normalized_remote = normalize_checker_config(checker_config)
        normalized_client = normalize_checker_config(client_config or checker_config)
        self._agent_checker_configs[normalized_agent_id] = {
            "remote": normalized_remote,
            "client": normalized_client,
        }
        return merge_checker_configs(normalized_remote, normalized_client) or {"phases": {}}

    def get_agent_checker_config(
        self,
        agent_id: str,
    ) -> dict[str, dict[str, Any] | None] | None:
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            return None
        current = self._agent_checker_configs.get(normalized_agent_id)
        if not current:
            return None
        remote_config = copy.deepcopy(current.get("remote"))
        client_config = copy.deepcopy(current.get("client"))
        return {
            "remote_checker_config": remote_config,
            "client_checker_config": client_config,
            "checker_config": merge_checker_configs(remote_config, client_config),
        }

    def update_client_checker_config(
        self,
        principal: dict[str, Any],
        checker_config: dict[str, Any],
        *,
        remote_checker_config: dict[str, Any] | None = None,
        timeout_s: float = 2.0,
    ) -> list[AuditTraceEntry]:
        matches = self.session_pool.find_by_principal(principal)
        updates: list[dict[str, Any]] = []
        for session in matches:
            session_id = session.get("session_id")
            agent_id = session.get("agent_id")
            user_id = session.get("user_id")
            config_copy = copy.deepcopy(checker_config)
            remote_copy = copy.deepcopy(remote_checker_config if remote_checker_config is not None else checker_config)
            self.session_pool.set_client_checker_config(
                str(session_id) if session_id else None,
                str(agent_id) if agent_id is not None else None,
                str(user_id) if user_id is not None else None,
                config_copy,
            )
            self.session_pool.set_remote_checker_config(
                str(session_id) if session_id else None,
                str(agent_id) if agent_id is not None else None,
                str(user_id) if user_id is not None else None,
                remote_copy,
            )
            url = session.get("client_config_url")
            if not url:
                updates.append(
                    {
                        "session_id": session_id,
                        "status": "skipped",
                        "reason": "no client config url",
                    }
                )
                continue
            pushed = _push_client_checker_config(
                str(url),
                config_copy,
                timeout_s,
                client_key=session.get("client_key"),
            )
            pushed["session_id"] = session_id
            updates.append(pushed)
        return updates

    def update_agent_checker_config(
        self,
        agent_id: str,
        checker_config: dict[str, Any],
        *,
        client_config: dict[str, Any] | None = None,
        timeout_s: float = 2.0,
    ) -> list[dict[str, Any]]:
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            return []
        self.set_agent_checker_config(
            normalized_agent_id,
            checker_config,
            client_config=client_config,
        )
        return self.update_client_checker_config(
            {"agent_id": normalized_agent_id},
            client_config or checker_config,
            remote_checker_config=checker_config,
            timeout_s=timeout_s,
        )

    def start_session_health_monitor(self) -> None:
        """Start the background session health monitor if it is not running."""
        if self._session_health_thread and self._session_health_thread.is_alive():
            return
        self._session_health_stop.clear()
        self._session_health_thread = threading.Thread(
            target=self._session_health_loop,
            name="agentguard-session-health",
            daemon=True,
        )
        self._session_health_thread.start()

    def stop_session_health_monitor(self) -> None:
        """Stop the background session health monitor."""
        self._session_health_stop.set()
        if self._session_health_thread and self._session_health_thread.is_alive():
            self._session_health_thread.join(timeout=1.0)

    def _session_health_loop(self) -> None:
        while not self._session_health_stop.wait(self._session_health_interval_s):
            try:
                self.refresh_stale_sessions(max_age_s=self._session_health_max_age_s)
            except Exception:
                pass

    def refresh_stale_sessions(
        self,
        *,
        max_age_s: float = 3600.0,
        timeout_s: float = 2.0,
    ) -> list[dict[str, Any]]:
        """Ping client health endpoints and refresh last_seen for live clients.

        ``max_age_s`` controls which sessions are checked. The background
        monitor uses ``0`` so every known session is checked every interval;
        manual callers may pass a larger value to check only stale sessions.
        """
        now = now_ts()
        results: list[dict[str, Any]] = []
        for session in self.session_pool.list():
            last_seen = float(session.get("last_seen") or 0)
            if now - last_seen < max_age_s:
                continue
            health_url = _client_health_url(session)
            if not health_url:
                results.append(
                    {
                        "session_id": session.get("session_id"),
                        "status": "skipped",
                        "reason": "no client health url",
                    }
                )
                continue
            alive, payload_or_error = _check_client_health(
                health_url,
                timeout_s,
                client_key=session.get("client_key"),
            )
            if alive:
                refreshed = self.session_pool.touch(
                    session.get("session_id"),
                    agent_id=session.get("agent_id"),
                    user_id=session.get("user_id"),
                    metadata={
                        "last_health_check_status": "ok",
                        "last_health_check_url": health_url,
                        "last_health_check_response": payload_or_error,
                    },
                )
                results.append(
                    {
                        "session_id": session.get("session_id"),
                        "status": "alive",
                        "health_url": health_url,
                        "last_seen": refreshed.get("last_seen") if refreshed else None,
                    }
                )
            else:
                results.append(
                    {
                        "session_id": session.get("session_id"),
                        "status": "unreachable",
                        "health_url": health_url,
                        "error": payload_or_error,
                    }
                )
        return results

    def _apply_agent_checker_config_to_session(
        self,
        session: dict[str, Any] | None,
    ) -> dict[str, Any]:
        current = dict(session or {})
        agent_id = str(current.get("agent_id") or "").strip()
        if not agent_id:
            return current
        overrides = self.get_agent_checker_config(agent_id)
        if not overrides:
            return current
        session_id = str(current.get("session_id") or "").strip() or None
        user_id = str(current.get("user_id")) if current.get("user_id") is not None else None
        if session_id and overrides.get("client_checker_config") is not None:
            updated = self.session_pool.set_client_checker_config(
                session_id,
                agent_id,
                user_id,
                overrides.get("client_checker_config"),
            )
            if updated:
                current = updated
        if session_id and overrides.get("remote_checker_config") is not None:
            updated = self.session_pool.set_remote_checker_config(
                session_id,
                agent_id,
                user_id,
                overrides.get("remote_checker_config"),
            )
            if updated:
                current = updated
        return current

    def _push_agent_checker_config_to_session(
        self,
        session: dict[str, Any] | None,
        *,
        timeout_s: float,
    ) -> dict[str, Any] | None:
        current = dict(session or {})
        url = current.get("client_config_url")
        checker_config = current.get("client_checker_config")
        if not url or not isinstance(checker_config, dict):
            return None
        return _push_client_checker_config(
            str(url),
            checker_config,
            timeout_s,
            client_key=current.get("client_key"),
        )

    def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx_dict = request.get("context") or {}
        context = RuntimeContext.from_dict(ctx_dict)
        event_dict = request.get("current_event") or {}
        self.register_client_session(
            context,
            client_ip=(request.get("_transport") or {}).get("client_ip"),
            client_key=(request.get("_transport") or {}).get("client_key"),
            enforce_key=bool((request.get("_transport") or {}).get("enforce_session_key")),
            event_dict=event_dict,
            push_config=False,
        )
        event = RuntimeEvent.from_dict(event_dict)
        # Bind the request-level context to the event so audit/observers see the
        # correct session/agent identity (current_event rarely embeds context).
        if ctx_dict:
            event.context = context
        cached_entries = list(request.get("client_cached_entries") or [])
        cached_events = _events_from_cached_entries(cached_entries)
        trace_window = _merge_event_window(
            cached_events + [
                RuntimeEvent.from_dict(e) for e in request.get("trajectory_window") or []
            ]
        )
        request["trajectory_window"] = [e.to_dict() for e in trace_window]
        if cached_entries:
            self.record_uploaded_trace(
                {
                    "session_id": context.session_id,
                    "agent_id": context.agent_id,
                    "user_id": context.user_id,
                    "reason": "decision_sync",
                    "entries": cached_entries,
                }
            )
        self._remember_trace_window(trace_window, context)

        session_cfg = self.session_pool.get(
            context.session_id or "",
            agent_id=context.agent_id,
            user_id=context.user_id,
        )
        effective_checker_config = session_cfg.get("remote_checker_config") if session_cfg else None
        agent_checker_config = self.get_agent_checker_config(context.agent_id or "")
        if agent_checker_config and agent_checker_config.get("remote_checker_config") is not None:
            effective_checker_config = agent_checker_config.get("remote_checker_config")
        effective_checkers = self.checkers
        if effective_checker_config is not None:
            effective_checkers = server_checker_manager(effective_checker_config)
            self._bind_rule_based_checkers_for(effective_checkers)

        for sig in request.get("local_signals") or []:
            event.add_signal(sig)

        check = effective_checkers.run(
            event,
            context,
            trajectory_window=trace_window,
            stop_on_first_decision=True,
        )
        decision = _decision_from_checker_result(check)

        # 2. Degrade plan if needed.
        if decision.decision_type == DecisionType.DEGRADE:
            plan = self.degrade.plan(
                event.payload.get("tool_name", ""), event.payload.get("arguments") or {}, decision.reason
            )
            decision.metadata["degrade_plan"] = plan.to_dict()

        # 3. Audit.
        self.audit.record(event.to_dict(), decision.to_dict())
        self._store_trace_record(
            context.session_id or event.context.session_id or "unknown",
            AuditTraceEntry(
                session_id=context.session_id or event.context.session_id or "unknown",
                agent_id=context.agent_id or event.context.agent_id,
                user_id=context.user_id or event.context.user_id,
                reason="guard_decide",
                event=event,
                decision=decision,
                checker_result=_checker_result_dict(check),
            ),
            agent_id=context.agent_id or event.context.agent_id,
            user_id=context.user_id or event.context.user_id,
        )

        # 3b. Observers (traffic/telemetry/approvals for the console).
        for observer in self.observers:
            try:
                observer(event, decision, request)
            except Exception:
                pass

        # 4. Response.
        risk_signals = sorted(set(event.risk_signals) | set(check.risk_signals))
        return {
            "decision": decision.to_dict(),
            "risk_signals": risk_signals,
            "checker_result": _checker_result_dict(check),
        }

    def get_trace_records(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.trace_store.get(session_id, agent_id=agent_id, user_id=user_id)

    def record_uploaded_trace(self, trace: dict[str, Any]) -> int:
        session_id = trace.get("session_id") or "unknown"
        agent_id = trace.get("agent_id") or (trace.get("_transport") or {}).get("agent_id")
        user_id = trace.get("user_id") or (trace.get("_transport") or {}).get("user_id")
        self.session_pool.touch(
            session_id,
            agent_id=str(agent_id) if agent_id is not None else None,
            user_id=str(user_id) if user_id is not None else None,
            client_ip=(trace.get("_transport") or {}).get("client_ip"),
            client_key=(trace.get("_transport") or {}).get("client_key"),
            enforce_key=bool((trace.get("_transport") or {}).get("enforce_session_key")),
            metadata={"last_trace_upload_reason": trace.get("reason")},
        )
        count = 0
        for entry in trace.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            record = AuditTraceEntry.from_dict(
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "reason": trace.get("reason"),
                    **entry,
                }
            )
            stored = self._store_trace_record(
                session_id,
                record,
                agent_id=str(record.agent_id) if record.agent_id is not None else None,
                user_id=str(record.user_id) if record.user_id is not None else None,
            )
            if not stored:
                continue
            if record.event is not None and record.decision is not None:
                self.audit.record(record.event.to_dict(), record.decision.to_dict())
            count += 1
        return count

    def _remember_trace_window(
        self,
        trace_window: list[RuntimeEvent],
        context: RuntimeContext,
    ) -> None:
        for observed in trace_window:
            observed_session_id = observed.context.session_id or context.session_id or "unknown"
            observed_agent_id = observed.context.agent_id or context.agent_id
            observed_user_id = observed.context.user_id or context.user_id
            self._store_trace_record(
                observed_session_id,
                AuditTraceEntry(
                    session_id=observed_session_id,
                    agent_id=observed_agent_id,
                    user_id=observed_user_id,
                    reason="trajectory_window",
                    event=observed,
                ),
                agent_id=observed_agent_id,
                user_id=observed_user_id,
            )

    def _store_trace_record(
        self,
        session_id: str,
        record: AuditTraceEntry | dict[str, Any],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        status = self.trace_store.upsert(
            session_id,
            record,
            agent_id=str(agent_id) if agent_id is not None else None,
            user_id=str(user_id) if user_id is not None else None,
        )
        return status != "unchanged"

    def _bind_rule_based_checkers(self) -> None:
        self._bind_rule_based_checkers_for(self.checkers)

    def _bind_rule_based_checkers_for(self, checker_manager: Any) -> None:
        try:
            from backend.runtime.checkers.tool_before.rule_based_check import RuleBasedChecker
        except Exception:
            return
        for checker in getattr(checker_manager, "checkers", []):
            if isinstance(checker, RuleBasedChecker):
                checker.set_policy_store(self.policy.store)


def _checker_result_dict(check: CheckResult) -> dict[str, Any]:
    return {
        "risk_signals": list(check.risk_signals),
        "is_final": check.is_final,
        "decision_candidate": (
            check.decision_candidate.to_dict() if check.decision_candidate else None
        ),
        "metadata": dict(check.metadata),
    }

def _decision_from_checker_result(check: CheckResult) -> GuardDecision:
    if check.is_final and check.decision_candidate is not None:
        return check.decision_candidate
    return GuardDecision.allow(
        "No server checker returned a final decision; default allow.",
        policy_id="server:no_final_checker",
        risk_signals=list(check.risk_signals),
        metadata={"explanation": "no final checker decision"},
    )


def _client_health_url(session: dict[str, Any]) -> str | None:
    if session.get("client_health_url"):
        return str(session["client_health_url"])
    config_url = session.get("client_config_url")
    if not config_url:
        return None
    parsed = urllib.parse.urlparse(str(config_url))
    if not parsed.scheme or not parsed.netloc:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/v1/client/health", "", "", ""))


def _check_client_health(
    url: str,
    timeout_s: float,
    *,
    client_key: str | None = None,
) -> tuple[bool, Any]:
    headers = {"Accept": "application/json"}
    if client_key:
        headers["X-AgentGuard-Session-Key"] = client_key
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(timeout_s, 0.1)) as response:
            payload = safe_loads(response.read(), fallback={}) or {}
        return payload.get("status") == "ok", payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def _push_client_checker_config(
    url: str,
    config: dict[str, Any],
    timeout_s: float,
    *,
    client_key: str | None = None,
) -> dict[str, Any]:
    body = safe_dumps({"config": config}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if client_key:
        headers["X-AgentGuard-Session-Key"] = str(client_key)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=max(timeout_s, 0.1)) as response:
            payload = safe_loads(response.read(), fallback={}) or {}
            return {
                "url": url,
                "status": "ok",
                "status_code": response.status,
                "response": payload,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "url": url,
            "status": "error",
            "status_code": exc.code,
            "error": raw.decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {"url": url, "status": "error", "error": str(exc)}


def _events_from_cached_entries(entries: list[dict[str, Any]]) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    for entry in entries:
        event_dict = _cached_entry_event_dict(entry)
        if not event_dict:
            continue
        try:
            events.append(RuntimeEvent.from_dict(event_dict))
        except Exception:
            continue
    return events


def _cached_entry_event_dict(entry: dict[str, Any]) -> dict[str, Any] | None:
    return trace_entry_event_dict(entry)


def _merge_event_window(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    merged: list[RuntimeEvent] = []
    seen: set[str] = set()
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        merged.append(event)
    return merged


def _trace_store_has_event(records: list[AuditTraceEntry | dict[str, Any]], event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    event_id = event.get("event_id")
    if not event_id:
        return False
    for record in records:
        rec_event = _cached_entry_event_dict(record)
        if rec_event and rec_event.get("event_id") == event_id:
            return True
    return False
