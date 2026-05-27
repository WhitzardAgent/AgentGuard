"""AgentGuard Runtime Server.

Two operating modes
-------------------
1. **In-process actor constellation** (``AgentGuardRuntime``). Spins up
   the full actor mesh (Ingress → Session → Policy → Decision → fan-out
   to Graph/Audit/Degrade/HumanReview) plus the four observability
   loops (Decision / Audit / DynamicRule / Review). Useful as the engine
   behind a FastAPI server when ``runtime_mode='async'`` is requested.

2. **Standalone HTTP service** (``AgentGuardServer``). Wraps a
   :class:`Guard` and exposes ``/v1/evaluate`` so remote agents can
   connect with::

       guard = Guard(remote_url="http://<host>:<port>", api_key="…")

   The server can run with the synchronous Pipeline (default,
   ``runtime_mode='sync'``) or the async actor runtime
   (``runtime_mode='async'``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from agentguard.audit.logger import AuditLogWriter
from agentguard.graph.builder import GraphWriter
from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.policy.rules.dynamic_store import SlowDispatcher
from agentguard.review.tickets import ApprovalBridge, InMemoryApprovalBridge
from agentguard.runtime.actors.audit_actor import AuditActor
from agentguard.runtime.actors.decision_actor import DecisionActor
from agentguard.runtime.actors.degrade_actor import DegradeActor
from agentguard.runtime.actors.dynamic_rule_actor import DynamicRuleActor
from agentguard.runtime.actors.graph_actor import GraphActor
from agentguard.runtime.actors.human_review_actor import HumanReviewActor
from agentguard.runtime.actors.policy_actor import PolicyActor
from agentguard.runtime.actors.session_actor import SessionActor
from agentguard.runtime.event_bus import EventBus
from agentguard.runtime.loops.audit_loop import AuditLoop
from agentguard.runtime.loops.decision_loop import DecisionLoop
from agentguard.runtime.loops.dynamic_rule_loop import DynamicRuleLoop
from agentguard.runtime.loops.ingress_loop import IngressLoop
from agentguard.runtime.loops.review_loop import ReviewLoop
from agentguard.storage.graph_store import GraphReadAPI, InMemoryGraphStore
from agentguard.storage.session_store import InMemoryStateCache, StateCache
from agentguard.storage.tool_catalog_store import InMemoryToolCatalogStore

if TYPE_CHECKING:
    from agentguard.models.decisions import Decision
    from agentguard.models.events import RuntimeEvent
    from agentguard.sdk.guard import Guard

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# AgentGuardRuntime  (in-process actor constellation)
# ─────────────────────────────────────────────────────────────────────────────

class AgentGuardRuntime:
    """Asynchronous actor + loop constellation.

    Components
    ----------
    ingress      : SDK / FastAPI entry point (see :class:`IngressLoop`)
    session      : per-event enrichment + feature extraction
    policy       : DSL evaluation
    decision     : decision routing + trace_log append + topic fan-out
    graph        : execution-graph maintenance (async writer)
    audit        : ring-buffer audit log
    degrade      : degrade-profile telemetry
    human_review : approval ticket creation
    dynamic_rule : LLM rule synthesis (gated by DynamicRuleLoop)

    Loops
    -----
    decision_loop     : metrics aggregation on ``make_decision``
    audit_loop        : optional drain to persistent sink
    dynamic_rule_loop : risk threshold + cooldown filter
    review_loop       : approval ticket timeout watchdog
    """

    def __init__(
        self,
        *,
        rules: Iterable[CompiledRule] | None = None,
        router: Any = None,
        cache: StateCache | None = None,
        graph_store: GraphReadAPI | None = None,
        mode: str = "enforce",
        allowlists: dict[str, Any] | None = None,
        # Optional shared infrastructure (lets a Guard hand its existing
        # writers down so the actor runtime and the synchronous Pipeline
        # observe the same audit / graph state).
        audit_writer: AuditLogWriter | None = None,
        graph_writer: GraphWriter | None = None,
        slow_dispatcher: SlowDispatcher | None = None,
        approval_bridge: ApprovalBridge | None = None,
        # Loop tunables
        review_timeout_s: float = 600.0,
        dynamic_risk_threshold: float = 0.6,
        dynamic_cooldown_s: float = 10.0,
        audit_flush_interval_s: float = 5.0,
    ) -> None:
        self.bus = EventBus()
        self._cache = cache or InMemoryStateCache()
        self._graph_store = graph_store or InMemoryGraphStore()

        self._audit_writer = audit_writer or AuditLogWriter()
        self._graph_writer = graph_writer or GraphWriter(self._graph_store, self._cache)
        self._slow = slow_dispatcher or SlowDispatcher()
        self._approval_bridge = approval_bridge or InMemoryApprovalBridge()

        rules_list = list(rules) if rules else []
        self._router = router

        # ── actors ──
        self.session_actor = SessionActor(
            self.bus, self._cache, self._graph_store,
            rules=rules_list, allowlists=allowlists, router=router,
        )
        self.policy_actor = PolicyActor(self.bus, rules_list, router=router)
        self.decision_actor = DecisionActor(self.bus, cache=self._cache, mode=mode)
        self.graph_actor = GraphActor(self.bus, self._graph_writer)
        self.dynamic_rule_actor = DynamicRuleActor(self.bus, self._slow)
        self.human_review_actor = HumanReviewActor(self.bus, self._approval_bridge)
        self.degrade_actor = DegradeActor(self.bus)
        self.audit_actor = AuditActor(self.bus, self._audit_writer)

        self._actors = [
            self.session_actor, self.policy_actor, self.decision_actor,
            self.graph_actor, self.dynamic_rule_actor, self.human_review_actor,
            self.degrade_actor, self.audit_actor,
        ]

        # ── loops ──
        self.ingress = IngressLoop(self.bus)
        self.decision_loop = DecisionLoop(self.bus)
        self.audit_loop = AuditLoop(
            self._audit_writer,
            flush_interval_s=audit_flush_interval_s,
        )
        self.dynamic_rule_loop = DynamicRuleLoop(
            self.bus,
            risk_threshold=dynamic_risk_threshold,
            cooldown_s=dynamic_cooldown_s,
        )
        self.review_loop = ReviewLoop(
            self._approval_bridge,
            timeout_s=review_timeout_s,
        )

        self._loops = [
            self.decision_loop,
            self.audit_loop,
            self.dynamic_rule_loop,
            self.review_loop,
            self.ingress,  # ingress last so consumers are ready
        ]
        self._started = False

    @classmethod
    def from_guard(
        cls,
        guard: "Guard",
        *,
        review_timeout_s: float = 600.0,
        dynamic_risk_threshold: float = 0.6,
        dynamic_cooldown_s: float = 10.0,
        audit_flush_interval_s: float = 5.0,
    ) -> "AgentGuardRuntime":
        """Build a runtime that *shares* state with an existing Guard.

        The returned runtime reuses the guard's StateCache, GraphStore,
        AuditLogWriter, GraphWriter, SlowDispatcher, and ApprovalBridge —
        so observability surfaces such as ``/audit/recent`` see the same
        records regardless of whether ``handle_attempt`` ran on the
        synchronous Pipeline or via ``ingress.submit``.
        """
        return cls(
            rules=guard.active_rules(),
            router=getattr(guard, "_router", None),
            cache=guard._cache,
            graph_store=guard._graph_store,
            mode=guard.mode,
            allowlists=guard._allowlists,
            audit_writer=guard._audit,
            graph_writer=guard._graph_writer,
            slow_dispatcher=guard._slow,
            approval_bridge=guard._enforcer.approval_bridge(),
            review_timeout_s=review_timeout_s,
            dynamic_risk_threshold=dynamic_risk_threshold,
            dynamic_cooldown_s=dynamic_cooldown_s,
            audit_flush_interval_s=audit_flush_interval_s,
        )

    async def start(self) -> None:
        if self._started:
            return
        for actor in self._actors:
            await actor.start()
        for loop in self._loops:
            await loop.start()
        self._started = True
        log.info(
            "AgentGuard runtime started: %d actors, %d loops",
            len(self._actors), len(self._loops),
        )

    async def stop(self) -> None:
        if not self._started:
            return
        # Stop loops in reverse order (ingress first so no new work flows in).
        for loop in reversed(self._loops):
            await loop.stop()
        for actor in reversed(self._actors):
            await actor.stop()
        self._started = False
        log.info("AgentGuard runtime stopped")

    # ── lifecycle helpers ──────────────────────────────────────────────
    @property
    def started(self) -> bool:
        return self._started

    @property
    def audit(self) -> AuditLogWriter:
        return self._audit_writer

    @property
    def approval_bridge(self) -> ApprovalBridge:
        return self._approval_bridge

    def load_rules(self, rules: Iterable[CompiledRule]) -> None:
        rules_list = list(rules)
        self.policy_actor.load(rules_list)
        self.session_actor.load_rules(rules_list)

    async def submit(self, event: "RuntimeEvent", *, timeout_s: float | None = None) -> "Decision":
        """Convenience: forward to the ingress loop's submit() coroutine."""
        return await self.ingress.submit(event, timeout_s=timeout_s)

    def metrics(self) -> dict[str, Any]:
        """Aggregate every loop / actor exposing a metrics() method."""
        return {
            "started": self._started,
            "ingress": {
                "submitted": self.ingress.submitted,
                "inflight": self.ingress.inflight,
            },
            "decisions": self.decision_loop.metrics(),
            "audit": self.audit_loop.metrics(),
            "dynamic_rule": self.dynamic_rule_loop.metrics(),
            "review": self.review_loop.metrics(),
            "degrade": self.degrade_actor.metrics(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentGuardServer  (standalone HTTP control-plane process)
# ─────────────────────────────────────────────────────────────────────────────

class AgentGuardServer:
    """Wraps Guard + FastAPI into a self-contained HTTP service.

    Remote agents connect with::

        guard = Guard(remote_url="http://<host>:<port>", api_key="...")

    The server exposes:

        POST /v1/evaluate          ← tool-call decision (hot path)
        POST /v1/evaluate/batch    ← batch evaluation
        GET  /health
        GET  /rules
        POST /rules/reload
        GET/POST /approvals/{id}/approve|deny
        GET  /audit/recent
        GET  /metrics              (async runtime mode only)

    Runtime modes:

    ``runtime_mode='sync'`` (default)
        Every ``/v1/evaluate`` POST runs straight through
        ``Guard.pipeline.handle_attempt(event)`` synchronously.

    ``runtime_mode='async'``
        Builds an :class:`AgentGuardRuntime` over the same Guard state
        and routes ``/v1/evaluate`` through ``runtime.submit(event)``,
        exercising the full actor / loop mesh.
    """

    def __init__(self, guard: "Guard", *, runtime_mode: str = "sync") -> None:
        if runtime_mode not in ("sync", "async"):
            raise ValueError(f"runtime_mode must be 'sync' or 'async', got {runtime_mode!r}")
        self._guard = guard
        self._runtime_mode = runtime_mode
        self._async_runtime: AgentGuardRuntime | None = None
        self._tool_catalog_store = InMemoryToolCatalogStore()

    @classmethod
    def from_policy(
        cls,
        policy_source: str | Path | None = None,
        *,
        builtin_rules: bool = True,
        mode: str = "enforce",
        api_key: str | None = None,
        allowlists: dict[str, Any] | None = None,
        runtime_mode: str = "sync",
        rule_pack_config: str | Path | None = None,
        state_cache_url: str | None = None,
        postgres_url: str | None = None,
    ) -> "AgentGuardServer":
        from agentguard.sdk.guard import Guard
        from agentguard.storage.session_store import build_state_cache

        state_cache = build_state_cache(state_cache_url)
        guard = Guard(
            policy_source=policy_source,
            builtin_rules=builtin_rules,
            mode=mode,
            allowlists=allowlists,
            state_cache=state_cache,
            llm_backend="env",
        )
        if api_key:
            guard._api_key = api_key  # type: ignore[attr-defined]
        if rule_pack_config:
            from agentguard.policy.rules.pack_loader import apply_rule_pack_config
            apply_rule_pack_config(guard, rule_pack_config)
        server = cls(guard, runtime_mode=runtime_mode)
        if postgres_url:
            from agentguard.storage.postgres import attach_postgres_backends
            attach_postgres_backends(server, postgres_url)
        return server

    def build_app(self) -> Any:
        from agentguard.api.routes import build_app
        return build_app(self._guard, server=self)

    @property
    def runtime_mode(self) -> str:
        return self._runtime_mode

    @property
    def async_runtime(self) -> AgentGuardRuntime | None:
        return self._async_runtime

    def serve(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 38080,
        log_level: str = "info",
        reload: bool = False,
    ) -> None:
        """Block and serve until interrupted. Requires uvicorn."""
        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "Serving requires uvicorn: pip install agentguard[server]"
            ) from e

        app = self.build_app()
        log.info(
            "AgentGuard Runtime listening on http://%s:%d (mode=%s)",
            host, port, self._runtime_mode,
        )
        uvicorn.run(app, host=host, port=port, log_level=log_level, reload=reload)

    def serve_in_thread(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 38080,
        ready_timeout: float = 5.0,
    ) -> "ServerHandle":
        """Start the server in a background thread (useful for tests / demos)."""
        import threading
        import time

        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "Serving requires uvicorn: pip install agentguard[server]"
            ) from e

        app = self.build_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        handle = ServerHandle(server=server, host=host, port=port, guard=self._guard)
        startup_errors: list[BaseException] = []

        def run_server() -> None:
            try:
                server.run()
            except BaseException as exc:  # pragma: no cover - exercised via thread lifecycle
                startup_errors.append(exc)

        t = threading.Thread(target=run_server, name="agentguard-http-server", daemon=True)
        t.start()
        handle._thread = t

        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if server.started:
                return handle
            if startup_errors or not t.is_alive() or server.should_exit:
                break
            time.sleep(0.05)

        handle.stop()
        detail = f" ({startup_errors[0]!r})" if startup_errors else ""
        raise RuntimeError(
            f"AgentGuard server failed to start on http://{host}:{port}. "
            "The port may already be in use, or the server exited before becoming ready."
            f"{detail}"
        )

    @property
    def guard(self) -> "Guard":
        return self._guard

    @property
    def tool_catalog_store(self) -> InMemoryToolCatalogStore:
        return self._tool_catalog_store

    # ─── async-runtime lifecycle (called from FastAPI lifespan) ──────────
    async def _ensure_async_runtime(self) -> AgentGuardRuntime:
        if self._async_runtime is None:
            self._async_runtime = AgentGuardRuntime.from_guard(self._guard)
        if not self._async_runtime.started:
            await self._async_runtime.start()
        return self._async_runtime

    async def _shutdown_async_runtime(self) -> None:
        if self._async_runtime is not None and self._async_runtime.started:
            await self._async_runtime.stop()

    def start_watcher(
        self,
        paths: list[str] | None = None,
        interval_s: float = 5.0,
        on_reload: "Callable[[int], None] | None" = None,
    ) -> "RuleWatcher":
        """Start the background rule-file watcher and return it.

        Parameters
        ----------
        paths:
            Directories/files to watch.  Defaults to the Guard's original
            ``policy_source`` paths.
        interval_s:
            Polling interval (used when *watchdog* is not installed).
        on_reload:
            Optional callback invoked after each successful reload.
        """
        from agentguard.runtime.watchers import RuleWatcher

        watch_paths: list[str] = []
        if paths:
            watch_paths = list(paths)
        else:
            src = getattr(self._guard, "_user_source", None)
            if src is not None:
                watch_paths = [str(src)] if isinstance(src, str) else list(str(p) for p in src)

        watcher = RuleWatcher(
            guard=self._guard,
            paths=watch_paths,
            interval_s=interval_s,
            on_reload=on_reload,
            async_runtime=self._async_runtime,
        )
        watcher.start()
        self._watcher = watcher
        return watcher

    def stop_watcher(self) -> None:
        """Stop the background rule-file watcher if running."""
        w = getattr(self, "_watcher", None)
        if w is not None:
            w.stop()
            self._watcher = None


class ServerHandle:
    """Handle returned by :meth:`AgentGuardServer.serve_in_thread`."""

    def __init__(self, *, server: Any, host: str, port: int, guard: "Guard") -> None:
        self._server = server
        self.host = host
        self.port = port
        self.guard = guard
        self._thread: Any = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=3.0)

    def __enter__(self) -> "ServerHandle":
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()
