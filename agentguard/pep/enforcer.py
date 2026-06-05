"""Dual-path Policy Enforcement Point.

Design
------
                         ┌─────────────── middleware (annotate + risk) ───────────┐
   RuntimeEvent ────────▶│                                                         │
                         └───────────────────────────┬─────────────────────────────┘
                                                      ▼
                                          ┌──────── decision cache ────────┐  hit ──▶ return
                                          └──────────────┬──────────────────┘
                                                         miss
                                                          ▼
                                   ┌──────────── FAST PATH (local) ───────────┐
                                   │  LocalEvaluator over synced PolicySnapshot │
                                   └──────────────┬─────────────────────────────┘
                                                  │ authoritative? ── yes ──▶ return (maybe async-prewarm PDP)
                                                  │ no (uncertain / high-risk)
                                                  ▼
                                   ┌──────── SLOW PATH (remote PDP) ──────────┐
                                   │  PDPClient.decide()  → merge(local,pdp)   │
                                   │  on failure → FallbackPolicy              │
                                   └───────────────────────────────────────────┘

* **fast_path** runs entirely on the client (local rules + cache) for low
  latency and offline resilience.
* **slow_path** escalates *only* uncertain or high-risk side-effecting events to
  the authoritative server PDP over the network.
* **async offload**: clearly-allowed events can still be sent to the PDP in the
  background to refresh the local decision cache, so repeat calls get the
  server's verdict on the fast path ("sinking" server policy into the client).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from agentguard.middleware.base import MiddlewareChain
from agentguard.pep.decision_cache import DecisionCache
from agentguard.pep.fallback import FallbackPolicy
from agentguard.pep.local_evaluator import LocalEvaluator
from agentguard.pdp_client.client import PDPClient, PDPUnavailable
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision, DecisionAction
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.schemas.risk import RiskAssessment
from agentguard.tools.downgrade import Downgrader

log = logging.getLogger("agentguard.pep")

_DEFAULT_ESCALATE_EVENTS = frozenset(
    {EventType.TOOL_CALL, EventType.NETWORK_ACTION, EventType.FILE_OP}
)
_DEFAULT_ESCALATE_ACTIONS = frozenset(
    {DecisionAction.ASK_USER, DecisionAction.REQUIRE_APPROVAL}
)


@dataclass
class EnforcerConfig:
    mode: str = "dual"  # "dual" | "local" | "pdp"
    escalate_risk_threshold: float = 0.6
    escalate_event_types: frozenset[EventType] = _DEFAULT_ESCALATE_EVENTS
    escalate_actions: frozenset[DecisionAction] = _DEFAULT_ESCALATE_ACTIONS
    async_prewarm: bool = True
    """When True, clearly-allowed escalatable events are sent to the PDP in the
    background to refresh the local decision cache."""


@dataclass
class EnforcementResult:
    decision: Decision
    event: RuntimeEvent  # possibly transformed (sanitized / degraded)
    risk: RiskAssessment
    path: str = "fast"  # "fast" | "slow" | "cache" | "fallback"

    @property
    def action(self) -> DecisionAction:
        return self.decision.action

    @property
    def allowed(self) -> bool:
        return not self.decision.action.blocks_execution


class Enforcer:
    def __init__(
        self,
        *,
        local_evaluator: LocalEvaluator,
        middleware: MiddlewareChain | None = None,
        pdp_client: PDPClient | None = None,
        cache: DecisionCache | None = None,
        fallback: FallbackPolicy | None = None,
        config: EnforcerConfig | None = None,
    ) -> None:
        self._local = local_evaluator
        self._middleware = middleware or MiddlewareChain()
        self._pdp = pdp_client
        self._cache = cache or DecisionCache()
        self._fallback = fallback or FallbackPolicy()
        self._downgrader = Downgrader()
        self.config = config or EnforcerConfig()
        self._prewarm_pool: ThreadPoolExecutor | None = (
            ThreadPoolExecutor(max_workers=2, thread_name_prefix="agentguard-prewarm")
            if self.config.async_prewarm
            else None
        )

    @property
    def local(self) -> LocalEvaluator:
        return self._local

    @property
    def pdp_enabled(self) -> bool:
        return self._pdp is not None and self._pdp.enabled

    # ════════════════════════════════════════════════════════════════════
    def enforce(self, event: RuntimeEvent, context: RuntimeContext) -> EnforcementResult:
        annotated, risk = self._middleware.run(event, context)

        version = self._local.snapshot.version
        cache_key = self._cache.key(annotated, context, version)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._finalize(cached, annotated, risk, path="cache")

        decision, path = self._decide(annotated, context, risk, cache_key)
        self._cache.put(cache_key, decision)
        return self._finalize(decision, annotated, risk, path=path)

    # ── path selection ──────────────────────────────────────────────────
    def _decide(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
        cache_key: str,
    ) -> tuple[Decision, str]:
        local_decision = self._local.evaluate(event, context)
        if risk.score > local_decision.risk_score:
            local_decision = local_decision.model_copy(update={"risk_score": risk.score})

        mode = self.config.mode
        if mode == "local" or not self.pdp_enabled:
            return local_decision, "fast"

        if mode == "pdp":
            return self._slow_path(event, context, local_decision)

        # mode == "dual"
        if self._should_escalate(event, local_decision, risk):
            return self._slow_path(event, context, local_decision)

        # Fast path wins; optionally refresh the cache from the PDP async.
        self._maybe_prewarm(event, context, local_decision, cache_key)
        return local_decision, "fast"

    def _should_escalate(
        self,
        event: RuntimeEvent,
        local_decision: Decision,
        risk: RiskAssessment,
    ) -> bool:
        if bool(event.annotations.get("escalate")):
            return True
        if event.type not in self.config.escalate_event_types:
            return False
        if local_decision.action in self.config.escalate_actions:
            return True
        if risk.score >= self.config.escalate_risk_threshold:
            return True
        return False

    def _slow_path(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        local_decision: Decision,
    ) -> tuple[Decision, str]:
        assert self._pdp is not None
        try:
            pdp_decision = self._pdp.decide(event, context)
        except PDPUnavailable as exc:
            log.warning("slow_path: PDP unavailable (%s); applying fallback", exc)
            return self._fallback.on_pdp_unavailable(local_decision), "fallback"
        # Stricter of the two wins (server authoritative, local as a safety net).
        return local_decision.merge(pdp_decision), "slow"

    def _maybe_prewarm(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        local_decision: Decision,
        cache_key: str,
    ) -> None:
        if self._prewarm_pool is None or not self.pdp_enabled:
            return
        if event.type not in self.config.escalate_event_types:
            return

        def _task() -> None:
            try:
                assert self._pdp is not None
                pdp_decision = self._pdp.decide(event, context)
            except PDPUnavailable:
                return
            merged = local_decision.merge(pdp_decision)
            merged = merged.model_copy(update={"source": "pdp-prewarm"})
            self._cache.put(cache_key, merged)

        try:
            self._prewarm_pool.submit(_task)
        except RuntimeError:  # pool shut down
            pass

    # ── obligations ─────────────────────────────────────────────────────
    def _finalize(
        self,
        decision: Decision,
        event: RuntimeEvent,
        risk: RiskAssessment,
        *,
        path: str,
    ) -> EnforcementResult:
        transformed = event
        if decision.action in (DecisionAction.SANITIZE, DecisionAction.DEGRADE):
            transformed = self._downgrader.apply(event, decision)
        return EnforcementResult(decision=decision, event=transformed, risk=risk, path=path)

    def close(self) -> None:
        if self._prewarm_pool is not None:
            self._prewarm_pool.shutdown(wait=False)
            self._prewarm_pool = None
