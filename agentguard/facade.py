"""AgentGuard — top-level façade for the client-side Harness / PEP runtime.

Wires together the event bus, runtime context, middleware chain, PEP enforcer
(local evaluator + optional remote PDP), execution sandbox, tool registry,
skill registry, plugin manager and audit recorder behind one ergonomic object.

Example
-------
    from agentguard import AgentGuard
    from agentguard.adapters import OpenAIAdapter
    from agentguard.skills.examples import SummarizeSkill

    guard = AgentGuard(session_id="s1", user_id="alice", policy="enterprise_default")
    agent = guard.wrap_agent(OpenAIAdapter(model="gpt-4"), enable_thought_hook=True)
    guard.register_skill(SummarizeSkill())
    print(agent.run("Analyze the report and summarize key points safely."))
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from agentguard.adapters.base import BaseAdapter
from agentguard.adapters.custom import CustomAdapter
from agentguard.audit.recorder import AuditRecorder
from agentguard.harness.agent_wrapper import GuardedAgent
from agentguard.harness.event_bus import EventBus
from agentguard.harness.lifecycle import Lifecycle, LifecycleStage
from agentguard.harness.llm_thought_hook import LLMThoughtHook
from agentguard.harness.runtime_context import use_context
from agentguard.harness.sandbox import Sandbox
from agentguard.harness.tool_wrapper import build_callable
from agentguard.middleware import default_middleware
from agentguard.middleware.base import Middleware, MiddlewareChain
from agentguard.pep.decision_cache import DecisionCache
from agentguard.pep.enforcer import EnforcementResult, Enforcer, EnforcerConfig
from agentguard.pep.fallback import FallbackPolicy
from agentguard.pep.local_evaluator import LocalEvaluator
from agentguard.pep.policy_snapshot import PolicySnapshot
from agentguard.pep.policy_sync import PolicySync
from agentguard.pdp_client.client import PDPClient
from agentguard.plugins.manager import PluginManager
from agentguard.policies.builtin import builtin_rules
from agentguard.policies.rule import Rule
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision, DecisionAction
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.skills.base import Skill, SkillRegistry, SkillResult
from agentguard.tools.capability import Capability
from agentguard.tools.metadata import ToolMetadata
from agentguard.tools.registry import ToolRegistry

log = logging.getLogger("agentguard.facade")

ApprovalHandler = Callable[[RuntimeEvent, Decision], bool]


class AgentGuard:
    def __init__(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        policy: str = "default",
        goal: str | None = None,
        scope: list[str] | None = None,
        builtin: bool = True,
        rules: list[Rule] | None = None,
        middleware: list[Middleware] | None = None,
        sandbox: bool = True,
        allowed_capabilities: list[str | Capability] | None = None,
        sandbox_strict: bool = False,
        sandbox_backend: str | Any = "local",
        sandbox_backend_options: dict[str, Any] | None = None,
        fail_open: bool = True,
        pdp_url: str | None = None,
        api_key: str = "",
        enforcer_mode: str = "dual",
        escalate_risk_threshold: float = 0.6,
        async_prewarm: bool = True,
        policy_sync: bool = True,
        policy_sync_interval: float = 10.0,
        audit_jsonl: str | Path | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.context = RuntimeContext(
            session_id=session_id or RuntimeContext().session_id,
            user_id=user_id,
            agent_id=agent_id,
            policy=policy,
            goal=goal,
            scope=list(scope or []),
            sandboxed=sandbox,
            fail_open=fail_open,
        )

        # ── event/audit/lifecycle plumbing ──────────────────────────────
        self.bus = EventBus()
        self.lifecycle = Lifecycle()
        self.audit = AuditRecorder(jsonl_path=audit_jsonl)

        # ── policy + PEP ────────────────────────────────────────────────
        self._rules: list[Rule] = (builtin_rules() if builtin else []) + list(rules or [])
        snapshot = PolicySnapshot(self._rules, policy_name=policy)
        self._local = LocalEvaluator(snapshot)
        self._chain = MiddlewareChain(middleware or default_middleware())
        self._cache = DecisionCache()
        self._pdp = PDPClient(pdp_url, api_key=api_key) if pdp_url else None
        self._enforcer = Enforcer(
            local_evaluator=self._local,
            middleware=self._chain,
            pdp_client=self._pdp,
            cache=self._cache,
            fallback=FallbackPolicy(fail_open=fail_open),
            config=EnforcerConfig(
                mode=enforcer_mode,
                escalate_risk_threshold=escalate_risk_threshold,
                async_prewarm=async_prewarm,
            ),
        )

        # ── policy sync (server → client fast-path coherence) ───────────
        self._policy_sync: PolicySync | None = None
        if self._pdp is not None and policy_sync:
            self._policy_sync = PolicySync(
                self._pdp, self._cache, interval_s=policy_sync_interval
            )
            self._policy_sync.start()

        # ── sandbox ─────────────────────────────────────────────────────
        # When sandbox is on and no explicit allowlist is given, start
        # restrictive: only zero-capability tools run until capabilities are
        # explicitly granted via allow_capabilities().
        allow = allowed_capabilities if allowed_capabilities is not None else ([] if sandbox else None)
        self._sandbox = Sandbox(
            enabled=sandbox,
            allowed_capabilities=allow,
            strict=sandbox_strict,
            backend=sandbox_backend,
            **(sandbox_backend_options or {}),
        )

        # ── registries / hooks ──────────────────────────────────────────
        self._tools = ToolRegistry()
        self._guarded_tools: dict[str, Callable[..., Any]] = {}
        self._skills = SkillRegistry()
        self._thought_hook = LLMThoughtHook(self)
        self._plugins = PluginManager(self)
        self._approval_handler = approval_handler

        self.lifecycle.fire(LifecycleStage.SESSION_START, self.context)

    # ════════════════════════════════════════════════════════════════════
    # Public attributes / passthroughs
    # ════════════════════════════════════════════════════════════════════
    @property
    def metadata(self) -> dict[str, Any]:
        return self.context.metadata

    @property
    def sandbox(self) -> Sandbox:
        return self._sandbox

    def allow_capabilities(self, *capabilities: str | Capability) -> None:
        """Explicitly grant capabilities to the sandbox."""
        self._sandbox.allow(*capabilities)

    def set_approval_handler(self, handler: ApprovalHandler) -> None:
        self._approval_handler = handler

    # ════════════════════════════════════════════════════════════════════
    # Agent + tool wrapping
    # ════════════════════════════════════════════════════════════════════
    def wrap_agent(self, agent: Any, *, enable_thought_hook: bool = True) -> GuardedAgent:
        """Wrap an LLM agent (a BaseAdapter, or any duck-typed agent) under
        full Harness enforcement."""
        adapter = agent if isinstance(agent, BaseAdapter) else CustomAdapter(agent)
        return GuardedAgent(self, adapter, enable_thought_hook=enable_thought_hook)

    def register_tool(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        sink_type: str = "none",
        capabilities: list[str] | None = None,
        **meta: Any,
    ) -> Callable[..., Any]:
        tool = self._tools.register(
            fn, name=name, sink_type=sink_type, capabilities=capabilities, **meta
        )
        guarded = build_callable(self, tool)
        self._guarded_tools[tool.metadata.name] = guarded
        return guarded

    def wrap_tool(
        self,
        *,
        name: str | None = None,
        sink_type: str = "none",
        capabilities: list[str] | None = None,
        **meta: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register_tool`."""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            return self.register_tool(
                fn, name=name, sink_type=sink_type, capabilities=capabilities, **meta
            )

        return deco

    def invoke_tool(self, name: str, **kwargs: Any) -> Any:
        guarded = self._guarded_tools.get(name)
        if guarded is None:
            raise KeyError(f"tool '{name}' is not registered")
        with use_context(self.context):
            return guarded(**kwargs)

    def tool_names(self) -> list[str]:
        return self._tools.names()

    def tool_metadata(self, name: str) -> ToolMetadata | None:
        tool = self._tools.get(name)
        return tool.metadata if tool else None

    # ════════════════════════════════════════════════════════════════════
    # Skills
    # ════════════════════════════════════════════════════════════════════
    def register_skill(self, skill: Skill) -> None:
        self._skills.register(skill)

    def skill_names(self) -> list[str]:
        return self._skills.names()

    def run_skill(self, name: str, **inputs: Any) -> SkillResult:
        skill = self._skills.get(name)
        if skill is None:
            return SkillResult(skill=name, ok=False, reason="skill_not_registered")

        event = RuntimeEvent(
            type=EventType.SKILL_INVOKED,
            session_id=self.context.session_id,
            user_id=self.context.user_id,
            agent_id=self.context.agent_id,
            tool_name=name,
            args=dict(inputs),
            payload={"skill": name},
        )
        self._dispatch_before(event)
        result = self._enforcer.enforce(event, self.context)
        self._dispatch_after(result)

        action = result.decision.action
        if action is DecisionAction.DENY:
            return SkillResult(skill=name, ok=False, reason=result.decision.reason)
        if action in (DecisionAction.ASK_USER, DecisionAction.REQUIRE_APPROVAL):
            if not self._request_approval(result.event, result.decision):
                return SkillResult(skill=name, ok=False, reason="approval_denied")

        # Skills honour DEGRADE/SANITIZE by routing through their own fallback
        # when policy reduces their inputs.
        run_inputs = dict(result.event.args) if result.event.args else dict(inputs)
        skill_result = skill.execute(self.context, **run_inputs)

        done = RuntimeEvent(
            type=EventType.SKILL_RESULT,
            session_id=self.context.session_id,
            tool_name=name,
            content=str(skill_result.output)[:500] if skill_result.output is not None else None,
            payload={"degraded": skill_result.degraded, "ok": skill_result.ok},
        )
        self.audit.record(done)
        self.bus.publish(done)
        return skill_result

    # ════════════════════════════════════════════════════════════════════
    # Extension points (used by plugins)
    # ════════════════════════════════════════════════════════════════════
    def register_middleware(self, middleware: Middleware) -> None:
        self._chain.add(middleware)
        self._cache.clear()

    def add_rule(self, rule: Rule) -> None:
        self._rules.append(rule)
        self._local.set_snapshot(PolicySnapshot(self._rules, policy_name=self.context.policy))
        self._cache.clear()

    def add_rules(self, rules: list[Rule]) -> None:
        self._rules.extend(rules)
        self._local.set_snapshot(PolicySnapshot(self._rules, policy_name=self.context.policy))
        self._cache.clear()

    def subscribe(self, event_type: EventType | str, handler: Callable[[RuntimeEvent], None]):
        return self.bus.subscribe(event_type, handler)

    def load_plugin(self, spec: Any) -> Any:
        return self._plugins.load(spec)

    @property
    def plugins(self) -> PluginManager:
        return self._plugins

    # ════════════════════════════════════════════════════════════════════
    # Introspection / lifecycle
    # ════════════════════════════════════════════════════════════════════
    def trace_rows(self) -> list[dict[str, Any]]:
        return self.audit.all_rows(self.context.session_id)

    def active_rules(self) -> list[Rule]:
        return list(self._rules)

    @property
    def policy_version(self) -> str | None:
        return self._policy_sync.current_version if self._policy_sync else None

    def close(self) -> None:
        self.lifecycle.fire(LifecycleStage.SESSION_END, self.context)
        if self._policy_sync is not None:
            self._policy_sync.stop()
        self._enforcer.close()
        self._sandbox.close()

    def __enter__(self) -> "AgentGuard":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ════════════════════════════════════════════════════════════════════
    # Internal hooks used by harness wrappers
    # ════════════════════════════════════════════════════════════════════
    def _dispatch_before(self, event: RuntimeEvent) -> None:
        self.lifecycle.fire(LifecycleStage.BEFORE_EVENT, event)
        self.bus.publish(event)

    def _dispatch_after(self, result: EnforcementResult) -> None:
        result.decision.metadata.setdefault("path", result.path)
        self.audit.record(result.event, result.decision)
        self.lifecycle.fire(LifecycleStage.ON_DECISION, result)
        self.lifecycle.fire(LifecycleStage.AFTER_EVENT, result.event, result.decision)

    def _request_approval(self, event: RuntimeEvent, decision: Decision) -> bool:
        if self._approval_handler is None:
            # Safe default: refuse anything needing explicit approval.
            log.info(
                "approval required for %s (%s) but no handler set → denying",
                event.summary(),
                decision.reason,
            )
            return False
        try:
            return bool(self._approval_handler(event, decision))
        except Exception as exc:  # noqa: BLE001
            log.warning("approval handler raised (%s); denying", exc)
            return False
