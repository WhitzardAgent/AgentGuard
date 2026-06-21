"""Client enforcer: local plugins first, then remote decision."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentguard.plugins.base import CheckResult
from agentguard.plugins.manager import PluginManager
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
    extensions: dict[str, Any] = field(default_factory=dict)


class UGuardEnforcer:
    """Client-side enforcement: final plugin verdict or server decision."""

    def __init__(
        self,
        *,
        snapshot: PolicySnapshot | None = None,
        remote: RemoteGuardClient | None = None,
        plugin_manager: PluginManager | None = None,
        trace_window_provider: Callable[[], list[RuntimeEvent]] | None = None,
        sync_buffer: ClientSyncBuffer | None = None,
        **_: Any,
    ) -> None:
        self.snapshot = snapshot
        self.remote = remote
        self.plugins = plugin_manager or PluginManager()
        self.trace_window_provider = trace_window_provider
        self.sync_buffer = sync_buffer or ClientSyncBuffer()

    def set_snapshot(self, snapshot: PolicySnapshot) -> None:
        self.snapshot = snapshot

    def update_plugin_config(self, config: str | Path | dict[str, Any] | None) -> None:
        """Replace local plugin configuration for subsequent events."""
        self.plugins.update_config(config)

    @property
    def server_available(self) -> bool:
        return bool(self.remote and self.remote.enabled and not self.remote.breaker.is_open)

    def enforce(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        *,
        extensions: dict[str, Any] | None = None,
        force_remote: bool = False,
    ) -> EnforcementResult:
        _ = force_remote

        # 1. Run local plugins. They can annotate the event with risk signals
        # and may return a final local decision.
        check = self.plugins.run(event, context)

        trace_window = self.trace_window_provider() if self.trace_window_provider else None

        # 2. A final plugin decision wins before remote.
        if check.is_final and check.decision_candidate is not None:
            decision = check.decision_candidate
            decision.metadata.setdefault("route", "local_plugin")
            self.sync_buffer.add_local_decision(
                event=event,
                context=context,
                check=check,
                decision=decision,
                route="local_plugin",
                extensions=extensions,
            )
            return EnforcementResult(
                decision,
                event,
                route="local_plugin",
                check=check,
                extensions=extensions or {},
            )

        # 3. No final local decision: send to remote and accept the server's
        # decision as authoritative.
        if self.server_available:
            decision, final_route = self._decide_remote(event, context, trace_window, extensions)
            return EnforcementResult(
                decision,
                event,
                route=final_route,
                check=check,
                extensions=extensions or {},
            )

        # 4. Local/dev mode without a remote server. This keeps wrappers usable
        # when no server_url is configured; production deployments should set
        # server_url so non-final events are judged by the server.
        decision = GuardDecision.allow(
            "No final local plugin decision and no remote server configured.",
            risk_signals=list(event.risk_signals),
            metadata={"route": "local_no_remote"},
        )
        return EnforcementResult(
            decision,
            event,
            route="local_no_remote",
            check=check,
            extensions=extensions or {},
        )

    # ---- helpers -------------------------------------------------------
    def _decide_remote(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trace_window: list[RuntimeEvent] | None,
        extensions: dict[str, Any] | None,
    ) -> tuple[GuardDecision, str]:
        try:
            cached_entries = self.sync_buffer.pop_all()
            decision = self.remote.decide(  # type: ignore[union-attr]
                event,
                context,
                trajectory_window=trace_window,
                local_signals=list(event.risk_signals),
                extensions=extensions or {},
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
