"""Server RuntimeManager: orchestrate a remote guard decision."""
from __future__ import annotations

import copy
import urllib.error
import urllib.parse
import urllib.request
import threading
from typing import Any, Callable

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import RuntimeEvent
from backend.audit.audit_logger import AuditLogger
from backend.plugins.loader import load_builtin_plugins
from backend.plugins.manager import PluginManager
from backend.runtime.checkers.base import CheckResult
from backend.runtime.checkers import server_checker_manager
from backend.runtime.degrade.planner import DegradePlanner
from backend.runtime.policy.engine import PolicyEngine
from backend.runtime.storage import SessionPool, TraceStore, trace_entry_event_dict
from shared.utils.json import safe_dumps, safe_loads
from shared.utils.time import now_ts


class RuntimeManager:
    """Coordinates checkers, plugins, policy and degradation server-side."""

    def __init__(
        self,
        *,
        policy: PolicyEngine | None = None,
        plugins: PluginManager | None = None,
        audit: AuditLogger | None = None,
        enable_agentdog: bool = True,
        checker_config: str | dict[str, Any] | None = None,
        session_health_interval_s: float = 1800.0,
        session_health_max_age_s: float = 0.0,
        enable_session_health_monitor: bool = True,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self.plugins = plugins or load_builtin_plugins(
            PluginManager(), enable_agentdog=enable_agentdog
        )
        self.checkers = server_checker_manager(checker_config)
        self.checker_config = checker_config
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
        # Observers receive (event, decision, request, plugin_results) after each
        # decision; used by the console for traffic/telemetry/approval tracking.
        self.observers: list[Callable[[RuntimeEvent, GuardDecision, dict, dict], None]] = []

    def add_observer(
        self, observer: Callable[[RuntimeEvent, GuardDecision, dict, dict], None]
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

    def register_client_session(self, context: RuntimeContext) -> dict[str, Any]:
        return self.session_pool.upsert(
            context,
            client_ip=(context.metadata or {}).get("client_ip"),
            client_key=(context.metadata or {}).get("client_session_key"),
        )

    def update_client_checker_config(
        self,
        principal: dict[str, Any],
        checker_config: dict[str, Any],
        *,
        remote_checker_config: dict[str, Any] | None = None,
        timeout_s: float = 2.0,
    ) -> list[dict[str, Any]]:
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

    def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx_dict = request.get("context") or {}
        context = RuntimeContext.from_dict(ctx_dict)
        event_dict = request.get("current_event") or {}
        self.session_pool.upsert(
            context,
            client_ip=(request.get("_transport") or {}).get("client_ip"),
            client_key=(request.get("_transport") or {}).get("client_key"),
            enforce_key=bool((request.get("_transport") or {}).get("enforce_session_key")),
            event_dict=event_dict,
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
        effective_checkers = self.checkers
        if effective_checker_config is not None:
            effective_checkers = server_checker_manager(effective_checker_config)
            self._bind_rule_based_checkers_for(effective_checkers)

        # 1. Server checkers add signals.
        check = effective_checkers.run(
            event,
            context,
            trajectory_window=trace_window,
            stop_on_first_decision=True,
        )

        # 2. Plugins: request lifecycle + diagnosis.
        plugin_ctx: dict[str, Any] = {"context": ctx_dict}
        request = self.plugins.on_request_received(request, plugin_ctx)
        request = self.plugins.on_before_policy_decision(request, plugin_ctx)
        plugin_results = self.plugins.diagnose(request, plugin_ctx)
        plugin_ctx["plugin_results"] = plugin_results

        # 3. Merge plugin-mapped risk signals into the event.
        for res in plugin_results.values():
            for sig in (res or {}).get("risk_signals", []) or []:
                event.add_signal(sig)
        for sig in request.get("local_signals") or []:
            event.add_signal(sig)

        # 4. Re-run configured checkers after plugin signals are attached. This
        # keeps optional rule-based checkers out of the core path while still
        # letting them see plugin-derived risk signals when they are enabled.
        post_plugin_check = effective_checkers.run(
            event,
            context,
            trajectory_window=trace_window,
            stop_on_first_decision=True,
        )
        check = _merge_check_results(check, post_plugin_check)
        decision = _decision_from_checker_result(check)
        decision = self.plugins.on_after_policy_decision(decision, plugin_ctx)

        # 5. Degrade plan if needed.
        if decision.decision_type == DecisionType.DEGRADE:
            plan = self.degrade.plan(
                event.payload.get("tool_name", ""), event.payload.get("arguments") or {}, decision.reason
            )
            decision.metadata["degrade_plan"] = plan.to_dict()

        # 6. Audit.
        self.audit.record(event.to_dict(), decision.to_dict(), plugin_results)
        self._store_trace_record(
            context.session_id or event.context.session_id or "unknown",
            {
                "session_id": context.session_id or event.context.session_id or "unknown",
                "agent_id": context.agent_id or event.context.agent_id,
                "user_id": context.user_id or event.context.user_id,
                "reason": "guard_decide",
                "event": event.to_dict(),
                "decision": decision.to_dict(),
                "checker_result": _checker_result_dict(check),
                "plugin_results": plugin_results,
            },
            agent_id=context.agent_id or event.context.agent_id,
            user_id=context.user_id or event.context.user_id,
        )

        # 6b. Observers (traffic/telemetry/approvals for the console).
        for observer in self.observers:
            try:
                observer(event, decision, request, plugin_results)
            except Exception:
                pass

        # 7. Response.
        risk_signals = sorted(set(event.risk_signals) | set(check.risk_signals))
        return {
            "decision": decision.to_dict(),
            "risk_signals": risk_signals,
            "checker_result": _checker_result_dict(check),
            "plugin_results": plugin_results,
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
            record = {
                "session_id": session_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "reason": trace.get("reason"),
                **entry,
            }
            event_dict = _cached_entry_event_dict(entry)
            entry_context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
            entry_agent_id = entry_context.get("agent_id", agent_id)
            entry_user_id = entry_context.get("user_id", user_id)
            stored = self._store_trace_record(
                session_id,
                record,
                agent_id=str(entry_agent_id) if entry_agent_id is not None else None,
                user_id=str(entry_user_id) if entry_user_id is not None else None,
            )
            if not stored:
                continue
            decision_dict = entry.get("decision") if isinstance(entry.get("decision"), dict) else None
            if event_dict and decision_dict:
                self.audit.record(event_dict, decision_dict, {"trace_upload": {"reason": trace.get("reason")}})
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
                {
                    "session_id": observed_session_id,
                    "agent_id": observed_agent_id,
                    "user_id": observed_user_id,
                    "reason": "trajectory_window",
                    "event": observed.to_dict(),
                },
                agent_id=observed_agent_id,
                user_id=observed_user_id,
            )

    def _store_trace_record(
        self,
        session_id: str,
        record: dict[str, Any],
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


def _merge_check_results(first: CheckResult, second: CheckResult) -> CheckResult:
    signals = list(first.risk_signals)
    for signal in second.risk_signals:
        if signal not in signals:
            signals.append(signal)
    metadata = dict(first.metadata)
    metadata.update(second.metadata)
    candidate = second.decision_candidate or first.decision_candidate
    is_final = first.is_final or second.is_final
    return CheckResult(
        decision_candidate=candidate,
        risk_signals=signals,
        is_final=is_final,
        metadata=metadata,
    )


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


def _trace_store_has_event(records: list[dict[str, Any]], event: dict[str, Any] | None) -> bool:
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
