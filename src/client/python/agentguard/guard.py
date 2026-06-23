"""AgentGuard: the public client facade."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Callable

from agentguard.adapters.llm import default_llm_adapters, select_llm_adapter
from agentguard.audit.logger import AuditLogger
from agentguard.audit.recorder import AuditRecorder
from agentguard.plugins.manager import PluginManager
from agentguard.config_api import ClientConfigAPIServer
from agentguard.harness.event_bus import EventBus
from agentguard.harness.lifecycle import Lifecycle
from agentguard.harness.runtime import HarnessRuntime
from agentguard.rules.loader import load_policy
from agentguard.sandbox.executor import SandboxExecutor
from agentguard.schemas.context import RuntimeContext
from agentguard.skill_client.registry_proxy import SkillRegistryProxy
from agentguard.skill_client.remote_runner import RemoteSkillRunner
from agentguard.tools.degrade import ToolDegradeManager
from agentguard.tools.metadata import ToolMetadata
from agentguard.tools.registry import ToolRegistry
from agentguard.tools.wrapper import ToolWrapper
from agentguard.u_guard.enforcer import UGuardEnforcer
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from agentguard.u_guard.remote_client import RemoteGuardClient


class AgentGuard:
    """Lightweight client-side Harness runtime."""

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
        max_steps: int = 12,
        max_tool_calls: int = 24,
        window_size: int = 8,
        audit_path: str | None = None,
        remote_timeout_s: float = 5.0,
        remote_retries: int = 2,
        plugin_config: str | dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        plugin_payload = _plugin_config_payload(plugin_config)
        snapshot = self._load_snapshot(policy)
        self.session_key = session_key or _generate_session_key()
        resolved_agent_id = agent_id or session_id
        self.context = RuntimeContext(
            session_id=session_id,
            user_id=user_id,
            agent_id=resolved_agent_id,
            policy=policy,
            policy_version=snapshot.version,
            environment=environment,
            metadata={
                "client_session_key": self.session_key,
                "client_plugin_config": plugin_payload,
                "remote_plugin_config": plugin_payload,
            },
        )

        self._remote = RemoteGuardClient(
            server_url,
            api_key=api_key,
            session_id=self.context.session_id,
            agent_id=self.context.agent_id,
            user_id=self.context.user_id,
            session_key=self.session_key,
            timeout_s=remote_timeout_s,
            retries=remote_retries,
        )
        self._enforcer = UGuardEnforcer(
            snapshot=snapshot,
            remote=self._remote,
            plugin_manager=PluginManager(config=plugin_config),
        )
        self._sandbox = SandboxExecutor(sandbox, sandbox_profile)
        self._audit = AuditRecorder(session_id, AuditLogger(audit_path))
        self._registry = ToolRegistry()
        self._degrade = ToolDegradeManager()
        self._lifecycle = Lifecycle()
        self._bus = EventBus()
        self._config_api: ClientConfigAPIServer | None = None

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

        self._llm_adapters = default_llm_adapters()
        self._skills = SkillRegistryProxy(
            remote=RemoteSkillRunner(
                server_url,
                api_key=api_key,
                session_id=self.context.session_id,
                agent_id=self.context.agent_id,
                user_id=self.context.user_id,
                session_key=self.session_key,
            )
            if server_url
            else None
        )
        self._register_remote_session()

    # ---- policy --------------------------------------------------------
    @staticmethod
    def _load_snapshot(policy: str | None) -> PolicySnapshot:
        rules = None
        if policy:
            for cand in (
                policy,
                f"rules/examples/{policy}.json",
                f"rules/examples/{policy}.rules",
                f"rules/{policy}.json",
                f"rules/{policy}.rules",
            ):
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

    def update_plugin_config(
        self,
        plugin_config: str | dict[str, Any] | None,
        *,
        sync_remote: bool = True,
    ) -> None:
        """Replace local plugin configuration for subsequent guarded events."""
        self.context.metadata["client_plugin_config"] = _plugin_config_payload(plugin_config)
        self._enforcer.update_plugin_config(plugin_config)
        if sync_remote:
            self._sync_remote_session()

    def start_config_api(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 38181,
        sync_remote: bool = True,
    ) -> str:
        """Start a local HTTP API for plugin configuration updates."""
        prev_config_url = self.context.metadata.get("client_config_url")
        prev_plugin_list_url = self.context.metadata.get("client_plugin_list_url")
        prev_health_url = self.context.metadata.get("client_health_url")
        if self._config_api is None:
            self._config_api = ClientConfigAPIServer(self, host=host, port=port)
        url = self._config_api.start()
        plugin_list_url = self._config_api.plugin_list_url
        health_url = self._config_api.health_url
        self.context.metadata["client_config_url"] = url
        self.context.metadata["client_plugin_list_url"] = plugin_list_url
        self.context.metadata["client_health_url"] = health_url
        urls_changed = (
            prev_config_url != url
            or prev_plugin_list_url != plugin_list_url
            or prev_health_url != health_url
        )
        if sync_remote and urls_changed:
            self._sync_remote_session()
        return url

    def stop_config_api(self) -> None:
        """Stop the local plugin configuration HTTP API if it is running."""
        if self._config_api is not None:
            self._config_api.stop()
            self._config_api = None
            self.context.metadata.pop("client_config_url", None)
            self.context.metadata.pop("client_plugin_list_url", None)
            self.context.metadata.pop("client_health_url", None)

    # ---- wrapping ------------------------------------------------------
    def wrap_tool(self, fn: Callable[..., Any], **meta: Any) -> ToolWrapper:
        metadata = self.register_tool(fn, **meta)
        return ToolWrapper(fn, metadata, self.runtime)

    def wrap_llm(self, llm: Any) -> Any:
        adapter = select_llm_adapter(llm, self._llm_adapters)
        return adapter.wrap(llm, self.runtime)

    def attach_autogen(
        self,
        agent: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch an AutoGen agent in-place while preserving its native loop."""
        from agentguard.adapters.agent.autogen import AutogenAgentAdapter  # noqa: PLC0415

        return AutogenAgentAdapter().attach(
            agent, self, wrap_tools=wrap_tools, wrap_llm=wrap_llm
        )

    def attach_langchain(
        self,
        agent: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch a LangChain agent in-place while preserving its native loop."""
        from agentguard.adapters.agent.langchain import LangChainAgentAdapter  # noqa: PLC0415

        return LangChainAgentAdapter().attach(
            agent, self, wrap_tools=wrap_tools, wrap_llm=wrap_llm
        )

    def attach_langgraph(
        self,
        agent: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch a LangGraph agent in-place while preserving its native graph loop."""
        from agentguard.adapters.agent.langgraph import LangGraphAgentAdapter  # noqa: PLC0415

        return LangGraphAgentAdapter().attach(
            agent, self, wrap_tools=wrap_tools, wrap_llm=wrap_llm
        )

    def attach_llamaindex(
        self,
        agent: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch a LlamaIndex workflow agent in-place while preserving its native loop."""
        from agentguard.adapters.agent.llamaindex import LlamaIndexAgentAdapter  # noqa: PLC0415

        return LlamaIndexAgentAdapter().attach(
            agent, self, wrap_tools=wrap_tools, wrap_llm=wrap_llm
        )

    def attach_openai_agents(
        self,
        agent: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch an OpenAI Agents SDK agent in-place while preserving Runner loop."""
        from agentguard.adapters.agent.openai_agents import OpenAIAgentsAdapter  # noqa: PLC0415

        return OpenAIAgentsAdapter().attach(
            agent, self, wrap_tools=wrap_tools, wrap_llm=wrap_llm
        )

    # ---- registration --------------------------------------------------
    def register_tool(self, fn: Callable[..., Any], **meta: Any) -> ToolMetadata:
        metadata = self._registry.register(fn, **meta)
        self._report_tool_metadata(metadata)
        return metadata

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
        self.runtime.sync_local_cache_now(reason="session_close")
        try:
            self._remote.unregister_session()
        except Exception:
            pass
        self.stop_config_api()

    def _report_tool_metadata(
        self,
        metadata: ToolMetadata,
        *,
        retry_on_sync: bool = True,
    ) -> None:
        if not self._remote.enabled:
            return
        tool_payload = {
            "name": metadata.name,
            "description": metadata.description,
            "input_params": list(metadata.required_args),
            "capabilities": list(metadata.capabilities),
            "labels": {
                "boundary": str(metadata.metadata.get("boundary", "internal")),
                "sensitivity": str(metadata.metadata.get("sensitivity", "low")),
                "integrity": str(metadata.metadata.get("integrity", "trusted")),
                "tags": [
                    str(tag)
                    for tag in (metadata.metadata.get("tags") or metadata.capabilities or [])
                    if str(tag).strip()
                ],
            },
        }
        try:
            self._remote.report_tool(self.context, tool_payload)
        except Exception:
            if retry_on_sync:
                self._sync_remote_session(report_tools=False)
                self._report_tool_metadata(metadata, retry_on_sync=False)
            return

    def _register_remote_session(self) -> None:
        if not self._remote.enabled:
            return
        try:
            self.start_config_api(port=0, sync_remote=False)
        except Exception:
            pass
        self._sync_remote_session()

    def _sync_remote_session(self, *, report_tools: bool = True) -> None:
        if not self._remote.enabled:
            return
        try:
            self._remote.register_session(self.context)
        except Exception:
            return
        if report_tools:
            self._report_registered_tools()

    def _report_registered_tools(self) -> None:
        for name in self._registry.names():
            metadata = self._registry.metadata(name)
            if metadata is None:
                continue
            self._report_tool_metadata(metadata, retry_on_sync=False)


def _generate_session_key() -> str:
    return f"sk-{secrets.token_urlsafe(32)}"


def _plugin_config_payload(
    plugin_config: str | Path | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if plugin_config is None:
        return None
    if isinstance(plugin_config, dict):
        return json.loads(json.dumps(plugin_config))
    path = Path(plugin_config)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("plugin config file must contain a JSON object")
    return data
