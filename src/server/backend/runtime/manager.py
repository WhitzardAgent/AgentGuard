"""Server RuntimeManager: orchestrate a remote guard decision."""
from __future__ import annotations

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
from backend.runtime.storage import TraceStore


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

    def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx_dict = request.get("context") or {}
        context = RuntimeContext.from_dict(ctx_dict)
        event = RuntimeEvent.from_dict(request.get("current_event") or {})
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
                    "reason": "decision_sync",
                    "entries": cached_entries,
                }
            )

        # 1. Server checkers add signals.
        check = self.checkers.run(event, context, trajectory_window=trace_window)

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
        post_plugin_check = self.checkers.run(event, context, trajectory_window=trace_window)
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

    def record_uploaded_trace(self, trace: dict[str, Any]) -> int:
        session_id = trace.get("session_id") or "unknown"
        count = 0
        for entry in trace.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            record = {
                "session_id": session_id,
                "reason": trace.get("reason"),
                **entry,
            }
            event_dict = _cached_entry_event_dict(entry)
            if _trace_store_has_event(self.trace_store.get(session_id), event_dict):
                continue
            self.trace_store.append(session_id, record)
            decision_dict = entry.get("decision") if isinstance(entry.get("decision"), dict) else None
            if event_dict and decision_dict:
                self.audit.record(event_dict, decision_dict, {"trace_upload": {"reason": trace.get("reason")}})
            count += 1
        return count

    def _bind_rule_based_checkers(self) -> None:
        try:
            from backend.runtime.checkers.tool_before.rule_based_check import RuleBasedChecker
        except Exception:
            return
        for checker in getattr(self.checkers, "checkers", []):
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
    event = entry.get("event")
    if isinstance(event, dict):
        return event
    checker_input = entry.get("checker_input")
    if isinstance(checker_input, dict) and isinstance(checker_input.get("event"), dict):
        return checker_input["event"]
    if isinstance(entry.get("event_type"), str):
        return entry
    return None


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
