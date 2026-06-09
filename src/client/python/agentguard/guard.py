"""AgentGuard: the public client facade."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentguard.adapters.agent import default_agent_adapters, select_agent_adapter
from agentguard.adapters.llm import default_llm_adapters, select_llm_adapter
from agentguard.audit.logger import AuditLogger
from agentguard.audit.recorder import AuditRecorder
from agentguard.checkers.manager import CheckerManager
from agentguard.harness.event_bus import EventBus
from agentguard.harness.lifecycle import Lifecycle
from agentguard.harness.runtime import HarnessRuntime
from agentguard.plugins.builtin.agentdog_proxy import AgentDoGProxyPlugin
from agentguard.plugins.manager import PluginManager
from agentguard.rules.loader import load_policy
from agentguard.sandbox.executor import SandboxExecutor
from agentguard.schemas.context import RuntimeContext
from agentguard.skill_client.registry_proxy import SkillRegistryProxy
from agentguard.skill_client.remote_runner import RemoteSkillRunner
from agentguard.tools.degrade import ToolDegradeManager
from agentguard.tools.metadata import ToolMetadata
from agentguard.tools.registry import ToolRegistry
from agentguard.tools.wrapper import ToolWrapper
from agentguard.u_guard.decision_cache import DecisionCache
from agentguard.u_guard.enforcer import UGuardEnforcer
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from agentguard.u_guard.remote_client import RemoteGuardClient


class AgentGuard:
    """Lightweight client-side Harness/U-Guard runtime."""

    def __init__(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        policy: str | None = None,
        server_url: str | None = None,
        api_key: str | None = None,
        environment: str | None = None,
        sandbox: str = "local",
        sandbox_profile: Any = None,
        enable_agentdog: bool = False,
        max_steps: int = 12,
        max_tool_calls: int = 24,
        window_size: int = 8,
        audit_path: str | None = None,
        remote_timeout_s: float = 5.0,
        remote_retries: int = 2,
    ) -> None:
        snapshot = self._load_snapshot(policy)
        self.context = RuntimeContext(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            policy=policy,
            policy_version=snapshot.version,
            environment=environment,
        )

        self._remote = RemoteGuardClient(
            server_url,
            api_key=api_key,
            timeout_s=remote_timeout_s,
            retries=remote_retries,
        )
        self._cache = DecisionCache()
        self._enforcer = UGuardEnforcer(
            snapshot=snapshot,
            remote=self._remote,
            checker_manager=CheckerManager(),
            cache=self._cache,
        )
        self._sandbox = SandboxExecutor(sandbox, sandbox_profile)
        self._audit = AuditRecorder(session_id, AuditLogger(audit_path))
        self._registry = ToolRegistry()
        self._degrade = ToolDegradeManager()
        self._lifecycle = Lifecycle()
        self._bus = EventBus()
        self._plugins = PluginManager(self._lifecycle)

        self.runtime = HarnessRuntime(
            context=self.context,
            enforcer=self._enforcer,
            sandbox=self._sandbox,
            audit=self._audit,
            registry=self._registry,
            degrade_manager=self._degrade,
            lifecycle=self._lifecycle,
            event_bus=self._bus,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            window_size=window_size,
        )

        self._agent_adapters = default_agent_adapters()
        self._llm_adapters = default_llm_adapters()
        self._skills = SkillRegistryProxy(
            remote=RemoteSkillRunner(server_url, api_key=api_key) if server_url else None
        )

        if enable_agentdog:
            self.register_plugin(AgentDoGProxyPlugin())
        self._plugins.start_session(self.context)

    # ---- policy --------------------------------------------------------
    @staticmethod
    def _load_snapshot(policy: str | None) -> PolicySnapshot:
        rules = None
        if policy:
            for cand in (policy, f"rules/examples/{policy}.json", f"rules/{policy}.json"):
                if cand and Path(cand).exists():
                    rules = load_policy(cand)
                    break
        if rules is None:
            rules = load_policy(None)
        return PolicySnapshot(version=policy or "builtin", rules=rules)

    def load_policy_snapshot(self, snapshot: PolicySnapshot | dict[str, Any]) -> None:
        snap = snapshot if isinstance(snapshot, PolicySnapshot) else PolicySnapshot.from_dict(snapshot)
        self._enforcer.set_snapshot(snap)
        self.context.policy_version = snap.version

    # ---- wrapping ------------------------------------------------------
    def wrap_tool(self, fn: Callable[..., Any], **meta: Any) -> ToolWrapper:
        metadata = self.register_tool(fn, **meta)
        return ToolWrapper(fn, metadata, self.runtime)

    def wrap_agent(self, agent: Any) -> Any:
        adapter = select_agent_adapter(agent, self._agent_adapters)
        return adapter.wrap(agent, self.runtime)

    def wrap_llm(self, llm: Any) -> Any:
        adapter = select_llm_adapter(llm, self._llm_adapters)
        return adapter.wrap(llm, self.runtime)

    # ---- registration --------------------------------------------------
    def register_tool(self, fn: Callable[..., Any], **meta: Any) -> ToolMetadata:
        return self._registry.register(fn, **meta)

    def register_plugin(self, plugin: Any) -> Any:
        return self._plugins.register(plugin)

    def register_skill(self, skill: Any) -> Any:
        try:
            from skills.registry import get_registry  # noqa: PLC0415

            get_registry().register(skill)
        except Exception:
            pass
        return skill

    # ---- skills --------------------------------------------------------
    def run_skill(self, skill_name: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._skills.run(skill_name, input_data or {})

    # ---- tools invocation (direct) ------------------------------------
    def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        reg = self._registry.get(tool_name)
        if reg is None:
            raise ValueError(f"tool not registered: {tool_name}")
        return self.runtime.invoke_tool(
            tool_name=tool_name, arguments=arguments, fn=reg.fn, metadata=reg.metadata
        )

    # ---- audit ---------------------------------------------------------
    def flush_audit(self) -> list[dict[str, Any]]:
        return self._audit.flush()

    @property
    def trace(self):
        return self.runtime.session.trace

    def close(self) -> None:
        self._plugins.end_session(self.runtime.session.trace, self.context)
