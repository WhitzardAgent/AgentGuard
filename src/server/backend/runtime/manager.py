"""Server RuntimeManager: orchestrate a remote guard decision."""
from __future__ import annotations

from typing import Any, Callable

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.events import RuntimeEvent
from backend.audit.audit_logger import AuditLogger
from backend.plugins.loader import load_builtin_plugins
from backend.plugins.manager import PluginManager
from backend.runtime.checkers.base import CheckResult
from backend.runtime.checkers import server_checker_manager
from backend.runtime.degrade.planner import DegradePlanner
from backend.runtime.policy.engine import PolicyEngine


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
        self.degrade = DegradePlanner()
        self.audit = audit or AuditLogger()
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

    def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx_dict = request.get("context") or {}
        context = RuntimeContext.from_dict(ctx_dict)
        event = RuntimeEvent.from_dict(request.get("current_event") or {})
        # Bind the request-level context to the event so audit/observers see the
        # correct session/agent identity (current_event rarely embeds context).
        if ctx_dict:
            event.context = context
        trace_window = [RuntimeEvent.from_dict(e) for e in request.get("trajectory_window") or []]

        # 1. Server checkers add signals.
        check = self.checkers.run(event, context)

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

        # 4. Policy decision (authoritative).
        decision = self.policy.decide(event, trace_window)
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


def _checker_result_dict(check: CheckResult) -> dict[str, Any]:
    return {
        "risk_signals": list(check.risk_signals),
        "is_final": check.is_final,
        "decision_candidate": (
            check.decision_candidate.to_dict() if check.decision_candidate else None
        ),
        "metadata": dict(check.metadata),
    }
