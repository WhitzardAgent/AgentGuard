"""Top-level facade: Guard wires every AgentGuard subsystem together.

Two deployment modes
─────────────────────
1. In-process (default):
       guard = Guard(policy_source="rules/", builtin_rules=True)
   All evaluation runs in the same Python process as the agent.

2. Remote (control-plane as service):
       guard = Guard(remote_url="http://runtime-host:38080", api_key="secret")
   Tool calls are forwarded to a standalone AgentGuardRuntime server via HTTP.
   The local process only needs agentguard installed — no policy files needed.
"""

from __future__ import annotations

import logging
import hashlib
from pathlib import Path
from typing import Any, Callable, Iterable

from agentguard.audit.logger import AuditLogWriter
from agentguard.degrade.planner import Enforcer, EnforcerConfig
from agentguard.graph.builder import GraphWriter
from agentguard.graph.provenance import ProvenanceTracker
from agentguard.models.decisions import Decision
from agentguard.models.events import EventType, Principal, RuntimeEvent
from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.policy.evaluator.matcher import FastEvaluator
from agentguard.policy.rules.dynamic_store import DynamicRuleConfig, SlowDispatcher
from agentguard.policy.rules.loaders import load_rules
from agentguard.policy.rules.registry import RuleRegistry
from agentguard.policy.rules.builtin import BUILTIN_RULES_DIR
from agentguard.policy.routing import (
    AgentBindingStore,
    InMemoryAgentBindingStore,
    RulePack,
    RuleRouter,
)
from agentguard.runtime.dispatcher import Pipeline
from agentguard.models.tool_catalog import ToolCatalogEntry, ToolCatalogLabels
from agentguard.sdk.context import current_session, session_scope, set_principal, push_session, pop_session
from agentguard.sdk.middleware import ToolMiddleware
from agentguard.sdk.wrappers import wrap_tool
from agentguard.storage.graph_store import GraphReadAPI, InMemoryGraphStore
from agentguard.storage.session_store import InMemoryStateCache, StateCache

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# RemotePipeline — thin proxy used in remote mode
# ─────────────────────────────────────────────────────────────────────────────

class RemotePipeline:
    """Mimics the Pipeline interface, but forwards every evaluate call to the
    remote AgentGuardServer via HTTP instead of running locally."""

    def __init__(self, client: Any, *, mode: str = "enforce") -> None:
        self._client = client
        self.mode = mode
        self._audit = AuditLogWriter()

    def handle_attempt(self, event: RuntimeEvent) -> Decision:
        decision = self._client.evaluate(event)
        self._audit.log(event, decision)
        return decision

    def handle_result(self, event: RuntimeEvent) -> None:
        self._audit.log(event)

    def record_event(self, event: RuntimeEvent) -> RuntimeEvent:
        ok = False
        try:
            ok = bool(self._client.record_event(event))
        except Exception as exc:
            log.warning("RemotePipeline: failed to record event remotely: %s", exc)
        self._audit.log(event)
        if not ok:
            log.debug("RemotePipeline: event recorded only in local audit mirror")
        return event

    def guarded_call(
        self,
        event: RuntimeEvent,
        original_executor: Callable[[RuntimeEvent], Any],
    ) -> Any:
        from agentguard.models.decisions import Action
        from agentguard.models.errors import DecisionDenied, HumanApprovalPending
        from agentguard.models.events import EventType

        decision = self.handle_attempt(event)

        if decision.action == Action.LLM_CHECK:
            raise HumanApprovalPending(
                ticket_id="remote_review",
                reason=decision.reason or "remote_llm_check_unresolved",
            )

        if self.mode == "monitor":
            return original_executor(event)

        if decision.action == Action.ALLOW:
            result = original_executor(event)
        elif decision.action == Action.DENY:
            raise DecisionDenied(
                reason=decision.reason or "policy_denied",
                matched_rules=decision.matched_rules,
                request_id=event.event_id,
            )
        elif decision.action == Action.HUMAN_CHECK:
            raise HumanApprovalPending(
                ticket_id="remote_review",
                reason=decision.reason or "human_check_required",
            )
        elif decision.action == Action.DEGRADE:
            # Apply degrade transforms locally (no enforcer in remote mode)
            from agentguard.degrade.transformers import ActionExecutor
            rewritten_tc = ActionExecutor().apply_rewrites(event, decision)
            if rewritten_tc and rewritten_tc != event.tool_call:
                event = event.with_tool_call(rewritten_tc)
            result = original_executor(event)
        else:
            result = original_executor(event)

        self.handle_result(event.model_copy(update={"event_type": EventType.TOOL_CALL_RESULT}))
        return result

    @property
    def audit(self) -> AuditLogWriter:
        return self._audit

    def close(self) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Guard  (user-facing façade)
# ─────────────────────────────────────────────────────────────────────────────

class Guard:
    """User-facing entrypoint for AgentGuard.

    Parameters
    ----------
    remote_url:
        If set, switch to **remote mode**: all evaluation requests are sent to
        a running AgentGuardServer via ``POST {remote_url}/v1/evaluate``.
        In this mode, ``policy_source`` / ``builtin_rules`` are ignored on the
        agent side — policies live on the server.
    api_key:
        Sent as ``X-Api-Key`` header in remote mode; also stored for the
        server-side auth check when this Guard powers an AgentGuardServer.
    fail_open:
        Remote mode only. If True (default), allow the tool call when the
        runtime is unreachable. Set False for strict fail-closed behaviour.
    remote_timeout:
        Per-request HTTP timeout in seconds (remote mode only). Default 10 s.
    llm_backend:
        Optional ``LLMBackend`` instance used for ``LLM_CHECK`` rule actions.
        When provided, the Enforcer invokes the LLM to review the event and
        resolve to ALLOW, DENY, or HUMAN_CHECK before responding to the caller.
        When omitted, ``LLM_CHECK`` falls back to the HUMAN_CHECK path.
    """

    def __init__(
        self,
        *,
        policy_source: str | Path | Iterable[str] | None = None,
        builtin_rules: bool = True,
        graph_backend: str | GraphReadAPI = "memory",
        state_cache: StateCache | None = None,
        mode: str = "enforce",
        allowlists: dict[str, Any] | None = None,
        enforcer_config: EnforcerConfig | None = None,
        dynamic_config: DynamicRuleConfig | None = None,
        # ── multi-pack rule routing ──────────────────────────────────────
        rule_packs: dict[str, str | Path | Iterable[str]] | None = None,
        agent_bindings: dict[str, Iterable[str]] | None = None,
        binding_store: AgentBindingStore | None = None,
        # ── LLM review backend (for LLM_CHECK rules) ─────────────────────
        llm_backend: Any | None = None,
        # ── optional plugins ──────────────────────────────────────────────
        plugins: Iterable[Any] | None = None,
        # ── remote mode ──────────────────────────────────────────────────
        remote_url: str | None = None,
        api_key: str = "",
        fail_open: bool = True,
        remote_timeout: float = 10.0,
    ) -> None:
        self.registry: dict[str, Callable[..., Any]] = {}
        self.mode = mode
        self._api_key = api_key
        self._dynamic: Any = None
        self._remote_client: Any | None = None
        self._plugins: list[Any] = []
        # token stored by start() so end_session() / close() can restore context
        self._session_token: Any = None

        # ── remote mode ──────────────────────────────────────────────────
        if remote_url:
            from agentguard.sdk.client import RemoteGuardClient
            self._remote_client = RemoteGuardClient(
                remote_url, api_key=api_key,
                timeout=remote_timeout, fail_open=fail_open,
            )
            self.pipeline: Pipeline | RemotePipeline = RemotePipeline(
                self._remote_client, mode=mode
            )
            for plugin in list(plugins or []):
                self.use_plugin(plugin)
            log.info("Guard: remote mode → %s", remote_url)
            return  # skip local subsystem init

        # ── in-process mode ──────────────────────────────────────────────
        self._cache = state_cache or InMemoryStateCache()
        self._graph_store = self._build_graph_store(graph_backend)
        self._router = RuleRouter(bindings=binding_store or InMemoryAgentBindingStore())
        self._rule_registry = RuleRegistry(router=self._router)
        self._allowlists = allowlists or {}
        self._builtin_on = builtin_rules

        builtin_loaded = (
            load_rules(BUILTIN_RULES_DIR, _is_builtin=True) if builtin_rules else []
        )
        self._router.replace_pack_rules(
            RuleRouter.BUILTIN_PACK_ID, builtin_loaded, source="builtin", user_managed=False
        )

        self._user_source = policy_source
        user_loaded: list[CompiledRule] = (
            load_rules(policy_source) if policy_source is not None else []
        )
        self._router.replace_pack_rules(
            RuleRouter.DEFAULT_PACK_ID,
            user_loaded,
            source=str(policy_source or ""),
            user_managed=False,
        )

        for pack_id, pack_source in (rule_packs or {}).items():
            self._router.replace_pack_rules(
                pack_id,
                load_rules(pack_source),
                source=str(pack_source),
                user_managed=False,
            )
        for agent_id, pack_ids in (agent_bindings or {}).items():
            for pack_id in pack_ids:
                if self._router.get_pack(pack_id) is None:
                    log.warning(
                        "Guard: agent %s bound to unknown pack %s; skipped",
                        agent_id, pack_id,
                    )
                    continue
                self._router.bind(agent_id, pack_id)

        self._fast = FastEvaluator(router=self._router)
        cfg = enforcer_config or EnforcerConfig(mode=mode)
        cfg.mode = mode
        self._enforcer = Enforcer(config=cfg, llm_backend=llm_backend)

        self._graph_writer = GraphWriter(self._graph_store, self._cache)
        self._audit = AuditLogWriter()
        self._slow = SlowDispatcher()
        self.provenance = ProvenanceTracker(self._cache)

        self.pipeline = Pipeline(
            cache=self._cache,
            graph=self._graph_store,
            fast_evaluator=self._fast,
            enforcer=self._enforcer,
            graph_writer=self._graph_writer,
            audit=self._audit,
            slow_dispatcher=self._slow,
            allowlists=self._allowlists,
        )

        if dynamic_config is not None:
            from agentguard.policy.rules.dynamic_store import DynamicRuleUpdater
            self._dynamic = DynamicRuleUpdater(guard=self, config=dynamic_config)
            self._dynamic.attach()

        plugin_list = list(plugins or [])
        if llm_backend is not None:
            from agentguard.plugins.llm_security import LLMSecurityReviewPlugin
            plugin_list.append(LLMSecurityReviewPlugin(llm_backend=llm_backend))
        for plugin in plugin_list:
            self.use_plugin(plugin)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------
    def tool(
        self,
        tool_name: str,
        *,
        sink_type: str = "none",
        boundary: str = "internal",
        sensitivity: str = "low",
        integrity: str = "trusted",
        tags: list[str] | None = None,
        tool_definition: dict[str, Any] | None = None,
        skill_manifest: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator that registers a tool with static labels.

        ``boundary``    : "internal" | "external" | "privileged"
        ``sensitivity`` : "low" | "moderate" | "high"
        ``integrity``   : "trusted" | "unfiltered"
        ``tags``        : free-form labels surfaced via ``tool.has_tag(...)``
        """
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            wrapped = wrap_tool(
                self, tool_name, fn,
                sink_type=sink_type,
                boundary=boundary, sensitivity=sensitivity,
                integrity=integrity, tags=tags,
                tool_definition=tool_definition,
                skill_manifest=skill_manifest,
            )
            return self._record_tool_registration(tool_name, wrapped)
        return deco

    def register(
        self,
        tool_name: str,
        fn: Callable[..., Any],
        *,
        sink_type: str = "none",
        boundary: str = "internal",
        sensitivity: str = "low",
        integrity: str = "trusted",
        tags: list[str] | None = None,
        tool_definition: dict[str, Any] | None = None,
        skill_manifest: dict[str, Any] | None = None,
    ) -> Callable[..., Any]:
        wrapped = wrap_tool(
            self, tool_name, fn,
            sink_type=sink_type,
            boundary=boundary, sensitivity=sensitivity,
            integrity=integrity, tags=tags,
            tool_definition=tool_definition,
            skill_manifest=skill_manifest,
        )
        return self._record_tool_registration(tool_name, wrapped)

    def install_middleware(self, registry: dict[str, Any]) -> None:
        ToolMiddleware(self).install(registry)

    # ------------------------------------------------------------------
    # Plugins
    # ------------------------------------------------------------------
    def use_plugin(self, plugin: Any) -> Any:
        """Attach an optional plugin to this Guard instance."""
        setup = getattr(plugin, "setup", None)
        if not callable(setup):
            raise TypeError("plugin must expose setup(guard)")
        setup(self)
        self._plugins.append(plugin)
        return plugin

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------
    @staticmethod
    def session(**kwargs: Any) -> Any:
        return session_scope(**kwargs)

    def start(
        self,
        *,
        principal: "Principal",
        goal: str | None = None,
        scope: list[str] | None = None,
        session_id: str | None = None,
    ) -> "GuardSession":
        """Start a session imperatively — no ``with`` block needed.

        Stores a reset token internally; call :meth:`end_session` (or
        :meth:`close`) when the agent loop finishes to restore context.

        Typical agent-loop pattern::

            guard.start(principal=p, goal="process tasks")
            try:
                while True:
                    task = queue.get()
                    if task is None:
                        break
                    agent.run(task)
            finally:
                guard.close()

        If a session was already active (started with another :meth:`start`
        call) it is ended first before the new one begins.
        """
        from agentguard.models.sessions import GuardSession  # local import avoids cycle

        if self._session_token is not None:
            self.end_session()

        session, token = push_session(
            principal=principal,
            goal=goal,
            scope=scope,
            session_id=session_id,
        )
        self._session_token = token
        return session

    def end_session(self) -> None:
        """End the session that was started with :meth:`start`.

        Restores the context-var to its previous value (usually ``None``).
        Safe to call multiple times or when no session is active.
        """
        if self._session_token is not None:
            pop_session(self._session_token)
            self._session_token = None

    def set_principal(self, principal: "Principal") -> None:
        set_principal(principal)

    def clear_session(self, session_id: str) -> None:
        """Evict all cached signals and provenance labels for a completed session."""
        from agentguard.runtime.dispatcher import clear_session_signals
        clear_session_signals(session_id)
        if not isinstance(self.pipeline, RemotePipeline):
            self._cache.clear()  # InMemoryStateCache clears all, good enough for now

    # ------------------------------------------------------------------
    # Model activity audit
    # ------------------------------------------------------------------
    def record_model_input(
        self,
        *,
        messages: Any | None = None,
        context: Any | None = None,
        provider: str | None = None,
        model: str | None = None,
        raw: Any | None = None,
        principal: Principal | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return self._record_model_activity(
            "model_input",
            EventType.AGENT_STEP_STARTED,
            principal=principal,
            goal=goal,
            scope=scope,
            payload={
                "messages": messages,
                "context": context,
                "provider": provider,
                "model": model,
                "raw": raw,
            },
            extra=extra,
        )

    def record_model_output(
        self,
        *,
        output: Any | None = None,
        tool_calls: Any | None = None,
        provider: str | None = None,
        model: str | None = None,
        raw: Any | None = None,
        principal: Principal | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return self._record_model_activity(
            "model_output",
            EventType.AGENT_STEP_COMPLETED,
            principal=principal,
            goal=goal,
            scope=scope,
            payload={
                "output": output,
                "tool_calls": tool_calls,
                "provider": provider,
                "model": model,
                "raw": raw,
            },
            extra=extra,
        )

    def record_visible_thought(
        self,
        *,
        thought: Any,
        provider: str | None = None,
        model: str | None = None,
        raw: Any | None = None,
        principal: Principal | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return self._record_model_activity(
            "visible_thought",
            EventType.THOUGHT_PRODUCED,
            principal=principal,
            goal=goal,
            scope=scope,
            payload={
                "thought": thought,
                "provider": provider,
                "model": model,
                "raw": raw,
            },
            extra=extra,
        )

    def record_plan(
        self,
        *,
        plan: Any,
        provider: str | None = None,
        model: str | None = None,
        raw: Any | None = None,
        principal: Principal | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return self._record_model_activity(
            "plan",
            EventType.PLAN_PRODUCED,
            principal=principal,
            goal=goal,
            scope=scope,
            payload={
                "plan": plan,
                "provider": provider,
                "model": model,
                "raw": raw,
            },
            extra=extra,
        )

    def record_action_proposed(
        self,
        *,
        action: Any,
        tool_calls: Any | None = None,
        provider: str | None = None,
        model: str | None = None,
        raw: Any | None = None,
        principal: Principal | None = None,
        goal: str | None = None,
        scope: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return self._record_model_activity(
            "action_proposed",
            EventType.ACTION_PROPOSED,
            principal=principal,
            goal=goal,
            scope=scope,
            payload={
                "action": action,
                "tool_calls": tool_calls,
                "provider": provider,
                "model": model,
                "raw": raw,
            },
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Rule management (in-process mode only)
    # ------------------------------------------------------------------
    def add_rules(
        self,
        source: str | Path | Iterable[str],
        *,
        override: bool = True,
        pack_id: str = RuleRouter.DEFAULT_PACK_ID,
    ) -> int:
        """Add rules to ``pack_id`` (defaults to the user pack).

        ``override=True`` replaces matching ``rule_id`` entries inside the
        target pack; ``override=False`` skips ids already present in any
        loaded pack.
        """
        self._assert_local("add_rules")
        new_rules = load_rules(source)
        if not new_rules:
            return 0
        if pack_id == RuleRouter.BUILTIN_PACK_ID:
            raise ValueError("cannot mutate the built-in rule pack at runtime")
        existing_pack = self._router.get_pack(pack_id)
        bucket: dict[str, CompiledRule] = {
            r.rule_id: r for r in (existing_pack.rules if existing_pack else [])
        }
        added = 0
        if override:
            for r in new_rules:
                bucket[r.rule_id] = r
                added += 1
        else:
            global_ids = {r.rule_id for r in self._router.all_rules()}
            for r in new_rules:
                if r.rule_id in global_ids:
                    continue
                bucket[r.rule_id] = r
                added += 1
        self._router.replace_pack_rules(
            pack_id,
            list(bucket.values()),
            source=existing_pack.source if existing_pack else "api",
            user_managed=existing_pack.user_managed if existing_pack else True,
        )
        self._fast.invalidate()
        return added

    def add_rules_from_text(
        self,
        dsl: str,
        *,
        override: bool = True,
        pack_id: str = RuleRouter.DEFAULT_PACK_ID,
    ) -> int:
        return self.add_rules(dsl, override=override, pack_id=pack_id)

    def remove_rule(self, rule_id: str) -> bool:
        self._assert_local("remove_rule")
        ok = self._rule_registry.remove(rule_id)
        if ok:
            self._fast.invalidate()
        return ok

    def replace_rule_pack_rules(
        self,
        pack_id: str,
        rules: Iterable[CompiledRule],
        *,
        source: str = "",
        user_managed: bool | None = None,
    ) -> RulePack:
        """Replace the contents of one runtime rule pack."""
        self._assert_local("replace_rule_pack_rules")
        existing_pack = self._router.get_pack(pack_id)
        if pack_id == RuleRouter.BUILTIN_PACK_ID:
            raise ValueError("cannot mutate the built-in rule pack at runtime")
        pack = self._router.replace_pack_rules(
            pack_id,
            rules,
            source=source,
            user_managed=(
                existing_pack.user_managed
                if user_managed is None and existing_pack is not None
                else bool(user_managed)
            ),
        )
        self._fast.invalidate()
        return pack

    def ensure_rule_pack(
        self,
        pack_id: str,
        *,
        source: str = "",
        user_managed: bool = True,
    ) -> RulePack:
        """Ensure a named non-builtin pack exists."""
        self._assert_local("ensure_rule_pack")
        if pack_id == RuleRouter.BUILTIN_PACK_ID:
            raise ValueError("cannot create the built-in rule pack")
        existing_pack = self._router.get_pack(pack_id)
        if existing_pack is not None:
            return existing_pack
        pack = self._router.replace_pack_rules(
            pack_id,
            [],
            source=source,
            user_managed=user_managed,
        )
        self._fast.invalidate()
        return pack

    def reload_rules(
        self,
        policy_source: str | Path | Iterable[str] | None = None,
        *,
        keep_builtin: bool | None = None,
        user_managed: bool | None = None,
    ) -> int:
        """Reload built-ins and the default user pack.

        Custom rule packs (created via :meth:`add_rule_pack`) are left
        untouched. Use :meth:`add_rule_pack` to refresh those individually.
        """
        self._assert_local("reload_rules")
        use_builtin = self._builtin_on if keep_builtin is None else keep_builtin
        self._builtin_on = use_builtin
        builtin_loaded = (
            load_rules(BUILTIN_RULES_DIR, _is_builtin=True) if use_builtin else []
        )
        self._router.replace_pack_rules(
            RuleRouter.BUILTIN_PACK_ID, builtin_loaded, source="builtin", user_managed=False
        )
        src = policy_source if policy_source is not None else self._user_source
        existing_default = self._router.get_pack(RuleRouter.DEFAULT_PACK_ID)
        user_loaded: list[CompiledRule] = []
        if src is not None:
            self._user_source = src
            user_loaded = load_rules(src)
        self._router.replace_pack_rules(
            RuleRouter.DEFAULT_PACK_ID,
            user_loaded,
            source=str(src or ""),
            user_managed=(
                existing_default.user_managed
                if user_managed is None and existing_default is not None
                else bool(user_managed)
            ),
        )
        self._fast.invalidate()
        return len(builtin_loaded) + len(user_loaded)

    def active_rules(self) -> list[CompiledRule]:
        if isinstance(self.pipeline, RemotePipeline):
            return []
        return list(self._router.all_rules())

    # ------------------------------------------------------------------
    # Rule pack & agent binding management
    # ------------------------------------------------------------------
    @property
    def router(self) -> RuleRouter:
        """Direct access to the underlying :class:`RuleRouter`."""
        self._assert_local("router")
        return self._router

    def add_rule_pack(
        self,
        pack_id: str,
        source: str | Path | Iterable[str],
    ) -> RulePack:
        """Create or replace a named rule pack."""
        self._assert_local("add_rule_pack")
        if pack_id in (RuleRouter.BUILTIN_PACK_ID,):
            raise ValueError("pack id is reserved")
        rules = load_rules(source)
        pack = self._router.replace_pack_rules(
            pack_id,
            rules,
            source=str(source) if isinstance(source, (str, Path)) else "api",
            user_managed=True,
        )
        self._fast.invalidate()
        return pack

    def remove_rule_pack(self, pack_id: str) -> bool:
        self._assert_local("remove_rule_pack")
        if pack_id in (RuleRouter.BUILTIN_PACK_ID,):
            raise ValueError("cannot remove the built-in pack")
        ok = self._router.remove_pack(pack_id)
        if ok:
            self._fast.invalidate()
        return ok

    def list_rule_packs(self) -> list[RulePack]:
        self._assert_local("list_rule_packs")
        return self._router.list_packs()

    def bind_agent(self, agent_id: str, pack_id: str) -> None:
        """Attach ``agent_id`` to ``pack_id`` (many-to-many)."""
        self._assert_local("bind_agent")
        self._router.bind(agent_id, pack_id)
        self._fast.invalidate()

    def unbind_agent(self, agent_id: str, pack_id: str) -> bool:
        self._assert_local("unbind_agent")
        ok = self._router.unbind(agent_id, pack_id)
        if ok:
            self._fast.invalidate()
        return ok

    def packs_for_agent(self, agent_id: str) -> list[str]:
        self._assert_local("packs_for_agent")
        return self._router.packs_for_agent(agent_id)

    def rules_for_agent(self, agent_id: str) -> list[CompiledRule]:
        self._assert_local("rules_for_agent")
        return self._router.rules_for_agent(agent_id)

    def list_agent_bindings(self) -> dict[str, list[str]]:
        self._assert_local("list_agent_bindings")
        return {a: sorted(p) for a, p in self._router.bindings().list_all().items()}

    # ------------------------------------------------------------------
    # Dynamic rules
    # ------------------------------------------------------------------
    @property
    def dynamic(self) -> Any:
        return self._dynamic

    def apply_dynamic_rules(self, dsl_text: str) -> int:
        return self.add_rules_from_text(dsl_text, override=True)

    # ------------------------------------------------------------------
    # Framework adapters
    # ------------------------------------------------------------------
    def attach_autogen(self, agent: Any) -> Any:
        from agentguard.sdk.adapters.autogen import AutogenAdapter
        adapter = AutogenAdapter(self.pipeline, self)
        adapter.install(agent)
        return adapter

    def attach_dify(self, app: Any) -> Any:
        from agentguard.sdk.adapters.dify import DifyAdapter
        adapter = DifyAdapter(self.pipeline, self)
        adapter.install(app)
        return adapter

    def attach_openclaw(self, runtime: Any) -> Any:
        from agentguard.sdk.adapters.openclaw import OpenClawAdapter
        adapter = OpenClawAdapter(self.pipeline, self)
        adapter.install(runtime)
        return adapter

    def attach_langchain(self, agent: Any) -> Any:
        from agentguard.sdk.adapters.langchain import LangChainAdapter
        adapter = LangChainAdapter(self.pipeline, self)
        adapter.install(agent)
        return adapter

    def langchain_callback_handler(self) -> Any:
        """Return a LangChain callback handler that records model activity."""
        from agentguard.sdk.adapters.langchain import AgentGuardLangChainCallbackHandler
        return AgentGuardLangChainCallbackHandler(self)

    def attach_openai_agents(self, agent: Any) -> Any:
        """Attach AgentGuard to an OpenAI Agents SDK ``Agent`` (or duck-type)."""
        from agentguard.sdk.adapters.openai_agents import OpenAIAgentsAdapter
        adapter = OpenAIAgentsAdapter(self.pipeline, self)
        adapter.install(agent)
        return adapter

    def attach_custom_agents(self, agent: Any, custom_adapter: BaseAdapter) -> Any:
        """Attach AgentGuard to a custom agent framework using a user-defined adapter.

        The adapter must inherit from :class:`BaseAdapter` and implement the
        :meth:`install` method, which takes care of instrumenting the target
        framework's tool execution path to call back into the Guard pipeline.
        """
        adapter = custom_adapter(self.pipeline, self)
        adapter.install(agent)
        return adapter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        """End the current session (if started with :meth:`start`) and
        release all subsystem resources.

        Safe to call even if :meth:`start` was never used.
        """
        self.end_session()
        for plugin in reversed(self._plugins):
            teardown = getattr(plugin, "teardown", None)
            if callable(teardown):
                try:
                    teardown()
                except Exception as exc:
                    log.warning("Guard: plugin teardown failed: %s", exc)
        self._plugins.clear()
        if self._dynamic is not None:
            self._dynamic.detach()
            self._dynamic = None
        self.pipeline.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _assert_local(self, method: str) -> None:
        if isinstance(self.pipeline, RemotePipeline):
            raise RuntimeError(
                f"Guard.{method}() is only available in in-process mode. "
                "Use the /rules/reload endpoint on the runtime server instead."
            )

    def _record_tool_registration(
        self,
        tool_name: str,
        wrapped: Callable[..., Any],
    ) -> Callable[..., Any]:
        self.registry[tool_name] = wrapped
        if self._remote_client is None:
            return wrapped
        session = current_session()
        if session is None:
            raise RuntimeError(
                "Remote tool registration requires an active Guard session so "
                "owner_agent_id can be attached to the tool catalog entry."
            )
        entry = self._build_tool_catalog_entry(
            tool_name,
            wrapped,
            owner_agent_id=session.principal.agent_id,
        )
        if entry is not None:
            self._report_tool_registration(entry)
        return wrapped

    def _record_model_activity(
        self,
        kind: str,
        event_type: EventType,
        *,
        principal: Principal | None,
        goal: str | None,
        scope: list[str] | None,
        payload: dict[str, Any],
        extra: dict[str, Any] | None,
    ) -> RuntimeEvent:
        session = current_session()
        resolved_principal = (
            principal
            or (session.principal if session is not None else None)
            or Principal(agent_id="sdk-default", session_id="anon")
        )
        resolved_goal = goal if goal is not None else (session.goal if session else None)
        resolved_scope = list(scope if scope is not None else (session.scope if session else []))
        activity = {
            "kind": kind,
            "capture_policy": "full",
            **{key: value for key, value in payload.items() if value is not None},
        }
        digest_source = repr(activity).encode("utf-8", errors="replace")
        activity["content_hash"] = hashlib.sha256(digest_source).hexdigest()
        event_extra = dict(extra or {})
        event_extra["model_activity"] = activity
        event = RuntimeEvent(
            event_type=event_type,
            principal=resolved_principal,
            goal=resolved_goal,
            scope=resolved_scope,
            extra=event_extra,
        )
        return self.pipeline.record_event(event)

    def _build_tool_catalog_entry(
        self,
        tool_name: str,
        wrapped_fn: Callable[..., Any],
        *,
        owner_agent_id: str,
    ) -> ToolCatalogEntry | None:
        meta = getattr(wrapped_fn, "__agentguard__", {}) or {}
        name = str(meta.get("tool_name", tool_name) or tool_name).strip()
        if not name:
            return None
        return ToolCatalogEntry(
            owner_agent_id=owner_agent_id,
            name=name,
            labels=ToolCatalogLabels(
                boundary=str(meta.get("boundary", "internal")),
                sensitivity=str(meta.get("sensitivity", "low")),
                integrity=str(meta.get("integrity", "trusted")),
                tags=[str(tag) for tag in list(meta.get("tags", []) or [])],
            ),
            input_params=[str(param) for param in list(meta.get("syntax", []) or [])],
        )

    def _report_tool_registration(self, entry: ToolCatalogEntry) -> None:
        client = self._remote_client
        if client is None:
            return
        try:
            ok = client.upsert_tool(entry)
        except Exception as exc:
            log.warning("Guard: failed to report tool %s - %s", entry.name, exc)
            return
        if not ok:
            log.warning("Guard: remote runtime did not accept tool %s", entry.name)

    def _refresh_evaluators(self) -> None:
        """Compatibility hook: invalidate per-agent indexed views."""
        self._fast.invalidate()

    @staticmethod
    def _dedupe_rules(rules: list[CompiledRule]) -> list[CompiledRule]:
        out: dict[str, CompiledRule] = {}
        for r in rules:
            out[r.rule_id] = r
        return list(out.values())

    def _build_graph_store(self, backend: str | GraphReadAPI) -> Any:
        if not isinstance(backend, str):
            return backend
        if backend in ("memory", "in-memory", ""):
            return InMemoryGraphStore()
        if backend.startswith("neo4j://") or backend.startswith("bolt://"):
            log.warning("Neo4j backend not wired; falling back to in-memory store.")
            return InMemoryGraphStore()
        return InMemoryGraphStore()
