"""Backward-compatible public API shims used by README examples."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentguard.guard import AgentGuard
from agentguard.u_guard.dpop import DPoPKey
from agentguard.u_guard.remote_client import RemoteGuardClient


@dataclass(slots=True)
class Principal:
    """Compatibility identity object used by older examples and docs."""

    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    trust_level: int | None = None
    task_id: str | None = None
    environment: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_context_kwargs(self) -> dict[str, Any]:
        principal = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "role": self.role,
            "trust_level": self.trust_level,
        }
        metadata = dict(self.metadata)
        metadata.setdefault(
            "principal",
            {key: value for key, value in principal.items() if value is not None},
        )
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "environment": self.environment,
            "metadata": metadata,
        }


class Guard:
    """Compatibility facade that maps the historical Guard API onto AgentGuard."""

    def __init__(
        self,
        *,
        remote_url: str | None = None,
        server_url: str | None = None,
        api_key: str | None = None,
        policy: str | None = None,
        environment: str | None = None,
        mode: str = "enforce",
        fail_open: bool | None = None,
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
        ticket: str | None = None,
        user_ticket: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        resolved_server_url = server_url or remote_url
        if remote_url and server_url and remote_url != server_url:
            raise ValueError("remote_url and server_url must match when both are provided")
        self._config = {
            "server_url": resolved_server_url,
            "api_key": api_key,
            "policy": policy,
            "environment": environment,
            "sandbox": sandbox,
            "sandbox_profile": sandbox_profile,
            "max_steps": max_steps,
            "max_tool_calls": max_tool_calls,
            "window_size": window_size,
            "audit_path": audit_path,
            "remote_timeout_s": remote_timeout_s,
            "remote_retries": remote_retries,
            "plugin_config": plugin_config,
            "session_key": session_key,
            "user_ticket": user_ticket or ticket,
            "agent_id": agent_id,
            "user_id": user_id,
        }
        self.mode = mode
        self.fail_open = fail_open
        self._guard: AgentGuard | None = None
        self._ticket_dpop_key: DPoPKey | None = None

    def start(self, *, principal: Principal | None = None, goal: str | None = None) -> "Guard":
        if self._config["server_url"] and not self._config["user_ticket"]:
            raise ValueError(
                "ticket or user_ticket is required for remote runtime-auth integration. "
                "Generate a ticket in AgentGuard User Centre and pass it to Guard(ticket=...)."
            )
        if principal is None:
            if not self._config["user_ticket"]:
                raise TypeError("principal is required unless ticket/user_ticket is configured")
            principal = Principal(session_id="agentguard-ticket-bootstrap")
        runtime_issue = self._create_ticket_runtime_session(principal=principal, goal=goal)
        self._guard = self._build_guard(
            **principal.to_context_kwargs(),
            runtime_issue=runtime_issue,
        )
        self._guard.context.metadata["guard_mode"] = self.mode
        if self.fail_open is not None:
            self._guard.context.metadata["guard_fail_open"] = self.fail_open
        if goal:
            self._guard.context.metadata["goal"] = goal
        self._guard.context.task_id = self._guard.context.task_id or principal.task_id
        if getattr(self._guard._remote, "enabled", False) and runtime_issue is None:
            self._guard._sync_remote_session()
        return self

    def close(self) -> None:
        if self._guard is not None:
            self._guard.close()

    def attach_langchain(
        self,
        agent: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        guard = self._require_guard()
        if getattr(guard._remote, "enabled", False) and not getattr(
            guard._remote,
            "use_dpop_auth",
            False,
        ):
            raise ValueError(
                "ticket or user_ticket is required for remote LangChain integration. "
                "Generate a ticket in AgentGuard User Centre and pass it to Guard(ticket=...)."
            )
        return guard.attach_langchain(agent, wrap_tools=wrap_tools, wrap_llm=wrap_llm)

    def _build_guard(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
        environment: str | None = None,
        metadata: dict[str, Any] | None = None,
        runtime_issue: dict[str, Any] | None = None,
    ) -> AgentGuard:
        runtime_issue = dict(runtime_issue or {})
        dpop_key = self._ticket_dpop_key
        guard = AgentGuard(
            session_id=str(runtime_issue.get("session_id") or session_id),
            user_id=(
                str(runtime_issue.get("user_id"))
                if runtime_issue.get("user_id") is not None
                else user_id if user_id is not None else self._config["user_id"]
            ),
            agent_id=(
                str(runtime_issue.get("agent_id"))
                if runtime_issue.get("agent_id") is not None
                else agent_id if agent_id is not None else self._config["agent_id"]
            ),
            policy=self._config["policy"],
            server_url=self._config["server_url"],
            api_key=self._config["api_key"],
            environment=environment if environment is not None else self._config["environment"],
            sandbox=self._config["sandbox"],
            sandbox_profile=self._config["sandbox_profile"],
            max_steps=self._config["max_steps"],
            max_tool_calls=self._config["max_tool_calls"],
            window_size=self._config["window_size"],
            audit_path=self._config["audit_path"],
            remote_timeout_s=self._config["remote_timeout_s"],
            remote_retries=self._config["remote_retries"],
            plugin_config=self._config["plugin_config"],
            session_key=self._config["session_key"],
            user_ticket=None if runtime_issue else self._config["user_ticket"],
            session_token=runtime_issue.get("session_token"),
            dpop_proof_factory=dpop_key.proof if dpop_key is not None and runtime_issue else None,
            use_dpop_auth=bool(runtime_issue),
            legacy_identity_headers=not bool(runtime_issue),
            auto_register_session=not bool(runtime_issue),
        )
        if metadata:
            guard.context.metadata.update(metadata)
        return guard

    def _create_ticket_runtime_session(
        self,
        *,
        principal: Principal,
        goal: str | None,
    ) -> dict[str, Any] | None:
        ticket = self._config["user_ticket"]
        server_url = self._config["server_url"]
        if not ticket:
            return None
        if not server_url:
            raise ValueError("server_url or remote_url is required when ticket is configured")
        dpop_key = DPoPKey()
        self._ticket_dpop_key = dpop_key
        metadata = dict(principal.metadata or {})
        principal_payload = principal.to_context_kwargs().get("metadata", {}).get("principal")
        if isinstance(principal_payload, dict):
            metadata.setdefault("principal", principal_payload)
        if goal:
            metadata["goal"] = goal
        client = RemoteGuardClient(
            server_url,
            api_key=self._config["api_key"],
            dpop_proof_factory=dpop_key.proof,
            use_dpop_auth=True,
            legacy_identity_headers=False,
            timeout_s=self._config["remote_timeout_s"],
            retries=self._config["remote_retries"],
        )
        return client.create_runtime_session(
            {
                "provider": "langchain",
                "user_ticket": ticket,
                "metadata": metadata,
            }
        )

    def _require_guard(self) -> AgentGuard:
        if self._guard is None:
            raise RuntimeError("guard session not started; call start(principal=...) first")
        return self._guard

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_guard(), name)
