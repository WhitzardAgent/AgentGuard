"""Pipeline orchestrator: composes the four runtime services.

The hot path used to instantiate concrete subsystems directly. It now
depends on protocol-typed services declared in
:mod:`agentguard.runtime.services` so any of them can be swapped for a
remote/RPC implementation without changing this file.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from agentguard.audit.logger import AuditLogWriter
from agentguard.graph.builder import GraphWriter
from agentguard.models.decisions import Decision
from agentguard.models.events import EventType, RuntimeEvent
from agentguard.policy.evaluator.matcher import FastEvaluator
from agentguard.policy.rules.dynamic_store import SlowDispatcher
from agentguard.runtime.enrichment import (
    append_trace,
    compute_fast_features,
    enrich_event,
    update_trace_result,
)
from agentguard.runtime.services import (
    AuditService,
    EnforcerService,
    GraphService,
    PolicyService,
)
from agentguard.storage.graph_store import GraphReadAPI
from agentguard.storage.session_store import StateCache
from agentguard.telemetry.stats import get_stats

log = logging.getLogger(__name__)
_stats = get_stats()


# Session-wide runtime signals the host can push via ``set_session_signal``.
# Each entry carries its signals dict plus the timestamp of the last write.
# Entries older than _SIGNAL_TTL_S are evicted lazily on next access.
_SESSION_SIGNALS: dict[str, dict[str, Any]] = {}
_SESSION_SIGNAL_TS: dict[str, float] = {}
_SIGNAL_TTL_S: float = 3600.0   # 1 hour default; callers may override


def set_session_signal(session_id: str, name: str, value: Any = True) -> None:
    """Publish a semantic signal (``goal_drift``, ``scope_expansion`` …).

    Any active rule using ``goal_drift_detected()`` / ``scope_expansion_detected()``
    will read this value on its next evaluation.
    """
    _SESSION_SIGNALS.setdefault(session_id, {})[name] = value
    _SESSION_SIGNAL_TS[session_id] = time.time()


def clear_session_signals(session_id: str) -> None:
    _SESSION_SIGNALS.pop(session_id, None)
    _SESSION_SIGNAL_TS.pop(session_id, None)


def _gc_session_signals() -> None:
    """Evict stale signal entries (called on every handle_attempt)."""
    now = time.time()
    stale = [sid for sid, ts in _SESSION_SIGNAL_TS.items()
             if now - ts > _SIGNAL_TTL_S]
    for sid in stale:
        _SESSION_SIGNALS.pop(sid, None)
        _SESSION_SIGNAL_TS.pop(sid, None)


class Pipeline:
    """The hot-path conductor — synchronous fast-path evaluation.

    Composed from four service-typed dependencies (policy / enforcer /
    graph / audit) so each one can be swapped for an RPC client without
    touching the orchestration code.
    """

    def __init__(
        self,
        *,
        cache: StateCache,
        graph: GraphReadAPI,
        policy: PolicyService | None = None,
        enforcer: EnforcerService,
        graph_writer: GraphService,
        audit: AuditService,
        slow_dispatcher: SlowDispatcher | None = None,
        allowlists: dict[str, Any] | None = None,
        # Backwards-compat alias accepted by older callers.
        fast_evaluator: PolicyService | None = None,
    ) -> None:
        resolved_policy = policy or fast_evaluator
        if resolved_policy is None:
            raise TypeError("Pipeline requires a policy service")
        self._cache = cache
        self._graph = graph
        self._fast: PolicyService = resolved_policy
        self._enforcer = enforcer
        self._graph_writer = graph_writer
        self._audit = audit
        self._slow = slow_dispatcher or SlowDispatcher()
        self._allowlists = allowlists or {}

    def handle_attempt(self, event: RuntimeEvent) -> Decision:
        """Called by adapters BEFORE executing a tool. Must not block."""
        _gc_session_signals()
        started = time.perf_counter()
        enriched = self._enrich(event)
        if enriched.extra != event.extra:
            event.extra = dict(enriched.extra)
        features = self._fast_features(enriched)
        # Inject runtime signals (in-process only; actor mode handles its own).
        sig_map = _SESSION_SIGNALS.get(enriched.principal.session_id) or {}
        for name, val in sig_map.items():
            features[f"signal.{name}"] = val
        decision = self._fast.evaluate(enriched, features)

        # Synchronously append to the trace log so the next call's
        # ``trace()`` predicate sees this attempt without waiting for the
        # async GraphWriter to flush. We only record tool-call attempts.
        if enriched.tool_call is not None and enriched.event_type in (
            EventType.TOOL_CALL_ATTEMPT,
            EventType.TOOL_CALL_REQUESTED,
        ):
            append_trace(enriched, self._cache)

        self._graph_writer.submit(enriched, decision)
        self._slow.submit(enriched)
        self._audit.log(enriched, decision)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > 15:
            log.debug("fast-path budget exceeded: %.1fms event=%s", elapsed_ms, event.event_id)

        # ── telemetry ──────────────────────────────────────────────────────
        tool_name = (enriched.tool_call.tool_name if enriched.tool_call else "") or ""
        agent_id = enriched.principal.agent_id if enriched.principal else ""
        session_id = enriched.principal.session_id if enriched.principal else ""
        _stats.record(
            tool_name=tool_name,
            agent_id=agent_id,
            session_id=session_id,
            action=decision.action.value,
            matched_rules=list(decision.matched_rules),
            latency_ms=elapsed_ms,
            risk_score=decision.risk_score,
            reason=decision.reason or "",
        )
        log.debug(
            "pipeline tool=%s agent=%s action=%s rules=%s latency=%.1fms",
            tool_name, agent_id, decision.action.value,
            decision.matched_rules, elapsed_ms,
        )
        return decision

    def handle_result(self, event: RuntimeEvent) -> None:
        """Called AFTER a tool has produced a result."""
        self._graph_writer.submit(event)
        self._audit.log(event)

    def record_event(self, event: RuntimeEvent) -> RuntimeEvent:
        """Record a non-tool runtime event for audit, graph, and slow hooks.

        Model inputs/outputs, visible thoughts, plans, and proposed actions are
        observability events rather than executable tool attempts, so they do
        not go through the blocking enforcer path.
        """
        _gc_session_signals()
        enriched = self._enrich(event)
        if enriched.extra != event.extra:
            event.extra = dict(enriched.extra)
        self._graph_writer.submit(enriched)
        self._slow.submit(enriched)
        self._audit.log(enriched)
        return enriched

    def guarded_call(
        self,
        event: RuntimeEvent,
        original_executor: Callable[[RuntimeEvent], Any],
    ) -> Any:
        """Convenience: run the full attempt -> enforce -> result cycle."""
        decision = self.handle_attempt(event)

        def revalidate(new_event: RuntimeEvent) -> Decision:
            return self.handle_attempt(new_event)

        result = None
        try:
            result = self._enforcer.apply(
                event, decision, original_executor, revalidate=revalidate,
            )
        finally:
            # Back-fill the tool's return value into the rich trace so the
            # NEXT call can access it via history_result("tool_name") in rules.
            if event.tool_call is not None:
                update_trace_result(event, self._cache, result)
            self.handle_result(
                event.model_copy(update={"event_type": EventType.TOOL_CALL_RESULT})
            )
        return result

    # -------------------- context enrichment --------------------
    def _enrich(self, event: RuntimeEvent) -> RuntimeEvent:
        return enrich_event(event, self._cache)

    def _fast_features(self, event: RuntimeEvent) -> dict[str, Any]:
        agent_id = event.principal.agent_id if event.principal else ""
        scoped_rules = self._fast.rules_for_agent(agent_id)
        return compute_fast_features(
            event,
            cache=self._cache,
            graph=self._graph,
            rules=scoped_rules,
            allowlists=self._allowlists,
        )

    # -------------------- introspection --------------------
    @property
    def fast_evaluator(self) -> PolicyService:
        return self._fast

    @property
    def policy_service(self) -> PolicyService:
        return self._fast

    @property
    def enforcer(self) -> EnforcerService:
        return self._enforcer

    @property
    def audit(self) -> AuditService:
        return self._audit

    @property
    def graph_writer(self) -> GraphService:
        return self._graph_writer

    def close(self) -> None:
        self._graph_writer.close()
        self._slow.close()


# Re-exported for callers that still type-annotate against the concrete classes.
from agentguard.degrade.planner import Enforcer  # noqa: E402, F401
