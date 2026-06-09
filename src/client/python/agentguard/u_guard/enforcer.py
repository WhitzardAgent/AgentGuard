"""U-Guard enforcer: orchestrates the local/remote decision flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agentguard.checkers.base import CheckResult
from agentguard.checkers.manager import CheckerManager
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.u_guard.decision_cache import DecisionCache
from agentguard.u_guard.fallback import FallbackGuard
from agentguard.u_guard.local_engine import LocalGuardEngine
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from agentguard.u_guard.remote_client import RemoteGuardClient
from agentguard.u_guard.router import RouteTarget, UGuardRouter
from agentguard.utils.errors import RemoteGuardError


@dataclass
class EnforcementResult:
    decision: GuardDecision
    event: RuntimeEvent
    route: str = "local"
    check: CheckResult | None = None
    plugin_extensions: dict[str, Any] = field(default_factory=dict)


class UGuardEnforcer:
    """Client-side guard: normalize -> cache -> local -> route -> remote/fallback."""

    def __init__(
        self,
        *,
        snapshot: PolicySnapshot | None = None,
        remote: RemoteGuardClient | None = None,
        checker_manager: CheckerManager | None = None,
        cache: DecisionCache | None = None,
        router: UGuardRouter | None = None,
        fallback: FallbackGuard | None = None,
        trace_window_provider: Callable[[], list[RuntimeEvent]] | None = None,
    ) -> None:
        self.local_engine = LocalGuardEngine(snapshot)
        self.remote = remote
        self.checkers = checker_manager or CheckerManager()
        self.cache = cache or DecisionCache()
        self.router = router or UGuardRouter()
        self.fallback = fallback or FallbackGuard()
        self.trace_window_provider = trace_window_provider

    def set_snapshot(self, snapshot: PolicySnapshot) -> None:
        self.local_engine.set_snapshot(snapshot)

    @property
    def server_available(self) -> bool:
        return bool(self.remote and self.remote.enabled and not self.remote.breaker.is_open)

    def enforce(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        *,
        plugin_extensions: dict[str, Any] | None = None,
        force_remote: bool = False,
        use_cache: bool = True,
    ) -> EnforcementResult:
        # 1. Run local checkers (annotates event with risk signals).
        check = self.checkers.run(event, context)

        # 2. Decision cache.
        if use_cache:
            cached = self.cache.get(event)
            if cached is not None:
                cached.metadata.setdefault("route", "cache")
                return EnforcementResult(cached, event, route="cache", check=check)

        # 3. Local policy snapshot.
        trace_window = self.trace_window_provider() if self.trace_window_provider else None
        local_eval = self.local_engine.evaluate(event, trace_window)

        # 4. Merge checker final candidate.
        if check.is_final and check.decision_candidate is not None:
            decision = check.decision_candidate
            self._finalize(event, decision, "local", use_cache)
            return EnforcementResult(decision, event, route="local", check=check)

        # 5. Route.
        plugin_requests_remote = bool((plugin_extensions or {}).get("force_remote"))
        route = self.router.route(
            event,
            local_eval,
            check,
            server_available=self.server_available,
            plugin_requests_remote=plugin_requests_remote,
            force_remote=force_remote,
        )

        # 6/7. Remote or fallback.
        if route.target == RouteTarget.REMOTE:
            decision, final_route = self._decide_remote(
                event, context, trace_window, plugin_extensions, local_eval.decision
            )
        elif route.target == RouteTarget.FALLBACK:
            decision = self.fallback.decide(event)
            final_route = "fallback"
        else:
            decision = local_eval.decision
            final_route = "local"

        # 8. Cache + finalize.
        self._finalize(event, decision, final_route, use_cache)
        return EnforcementResult(
            decision, event, route=final_route, check=check,
            plugin_extensions=plugin_extensions or {},
        )

    # ---- helpers -------------------------------------------------------
    def _decide_remote(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trace_window: list[RuntimeEvent] | None,
        plugin_extensions: dict[str, Any] | None,
        local_decision: GuardDecision,
    ) -> tuple[GuardDecision, str]:
        try:
            decision = self.remote.decide(  # type: ignore[union-attr]
                event,
                context,
                trajectory_window=trace_window,
                local_signals=list(event.risk_signals),
                plugin_extensions=plugin_extensions or {},
            )
            decision.metadata.setdefault("route", "remote")
            return self._merge_strict(local_decision, decision), "remote"
        except RemoteGuardError:
            return self.fallback.decide(event), "fallback"

    @staticmethod
    def _merge_strict(local: GuardDecision, remote: GuardDecision) -> GuardDecision:
        """Deny-overrides: keep the stricter of local and remote."""
        from agentguard.rules.matcher import _EFFECT_RANK  # noqa: PLC0415

        # Map decision types to a rough strictness rank.
        rank = {
            DecisionType.DENY: 9,
            DecisionType.REQUIRE_APPROVAL: 8,
            DecisionType.REQUIRE_REMOTE_REVIEW: 7,
            DecisionType.ASK_USER: 7,
            DecisionType.DEGRADE: 6,
            DecisionType.SANITIZE: 5,
            DecisionType.REWRITE: 4,
            DecisionType.REPAIR: 3,
            DecisionType.LOG_ONLY: 2,
            DecisionType.ALLOW: 1,
        }
        _ = _EFFECT_RANK  # keep import meaningful for parity with rule matcher
        if rank.get(local.decision_type, 0) > rank.get(remote.decision_type, 0):
            local.metadata.setdefault("remote_decision", remote.decision_type.value)
            return local
        return remote

    def _finalize(
        self, event: RuntimeEvent, decision: GuardDecision, route: str, use_cache: bool
    ) -> None:
        decision.metadata.setdefault("route", route)
        if use_cache:
            self.cache.put(event, decision)
