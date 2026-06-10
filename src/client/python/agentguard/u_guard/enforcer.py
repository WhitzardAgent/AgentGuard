"""Client enforcer: local checkers first, then remote decision."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentguard.checkers.base import CheckResult
from agentguard.checkers.manager import CheckerManager
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from agentguard.u_guard.remote_client import RemoteGuardClient
from agentguard.u_guard.sync_buffer import ClientSyncBuffer
from agentguard.utils.errors import RemoteGuardError


@dataclass
class EnforcementResult:
    decision: GuardDecision
    event: RuntimeEvent
    route: str = "local"
    check: CheckResult | None = None
    plugin_extensions: dict[str, Any] = field(default_factory=dict)


class UGuardEnforcer:
    """Client-side enforcement: final checker verdict or server decision."""

    def __init__(
        self,
        *,
        snapshot: PolicySnapshot | None = None,
        remote: RemoteGuardClient | None = None,
        checker_manager: CheckerManager | None = None,
        trace_window_provider: Callable[[], list[RuntimeEvent]] | None = None,
        sync_buffer: ClientSyncBuffer | None = None,
        **_: Any,
    ) -> None:
        self.snapshot = snapshot
        self.remote = remote
        self.checkers = checker_manager or CheckerManager()
        self.trace_window_provider = trace_window_provider
        self.sync_buffer = sync_buffer or ClientSyncBuffer()

    def set_snapshot(self, snapshot: PolicySnapshot) -> None:
        self.snapshot = snapshot

    def update_checker_config(self, config: str | Path | dict[str, Any] | None) -> None:
        """Replace local checker configuration for subsequent events."""
        self.checkers.update_config(config)

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
    ) -> EnforcementResult:
        _ = force_remote

        # 1. Run local checkers. They can annotate the event with risk signals
        # and may return a final local decision.
        check = self.checkers.run(event, context)

        trace_window = self.trace_window_provider() if self.trace_window_provider else None

        # 2. A final checker decision wins before remote.
        if check.is_final and check.decision_candidate is not None:
            decision = check.decision_candidate
            decision.metadata.setdefault("route", "local_checker")
            self.sync_buffer.add_local_decision(
                event=event,
                context=context,
                check=check,
                decision=decision,
                route="local_checker",
                plugin_extensions=plugin_extensions,
            )
            return EnforcementResult(
                decision,
                event,
                route="local_checker",
                check=check,
                plugin_extensions=plugin_extensions or {},
            )

        # 3. No final local decision: send to remote and accept the server's
        # decision as authoritative.
        if self.server_available:
            decision, final_route = self._decide_remote(
                event, context, trace_window, plugin_extensions
            )
            return EnforcementResult(
                decision,
                event,
                route=final_route,
                check=check,
                plugin_extensions=plugin_extensions or {},
            )

        # 4. Local/dev mode without a remote server. This keeps wrappers usable
        # when no server_url is configured; production deployments should set
        # server_url so non-final events are judged by the server.
        decision = GuardDecision.allow(
            "No final local checker decision and no remote server configured.",
            risk_signals=list(event.risk_signals),
            metadata={"route": "local_no_remote"},
        )
        return EnforcementResult(
            decision,
            event,
            route="local_no_remote",
            check=check,
            plugin_extensions=plugin_extensions or {},
        )

    # ---- helpers -------------------------------------------------------
    def _decide_remote(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trace_window: list[RuntimeEvent] | None,
        plugin_extensions: dict[str, Any] | None,
    ) -> tuple[GuardDecision, str]:
        try:
            cached_entries = self.sync_buffer.pop_all()
            decision = self.remote.decide(  # type: ignore[union-attr]
                event,
                context,
                trajectory_window=trace_window,
                local_signals=list(event.risk_signals),
                plugin_extensions=plugin_extensions or {},
                client_cached_entries=cached_entries,
            )
            decision.metadata.setdefault("route", "remote")
            return decision, "remote"
        except RemoteGuardError:
            self.sync_buffer.restore_front(cached_entries)
            decision = GuardDecision.require_remote_review(
                "Remote decision unavailable; event requires server judgement.",
                risk_signals=list(event.risk_signals),
                metadata={"route": "remote_unavailable"},
            )
            return decision, "remote_unavailable"
