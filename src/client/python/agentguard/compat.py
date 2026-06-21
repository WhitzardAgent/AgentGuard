"""Backward-compatible public API shims used by README examples."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentguard.guard import AgentGuard


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
            "agent_id": agent_id,
            "user_id": user_id,
        }
        self.mode = mode
        self.fail_open = fail_open
        self._guard: AgentGuard | None = None

    def start(self, *, principal: Principal, goal: str | None = None) -> "Guard":
        self._guard = self._build_guard(**principal.to_context_kwargs())
        self._guard.context.metadata["guard_mode"] = self.mode
        if self.fail_open is not None:
            self._guard.context.metadata["guard_fail_open"] = self.fail_open
        if goal:
            self._guard.context.metadata["goal"] = goal
        self._guard.context.task_id = self._guard.context.task_id or principal.task_id
        if getattr(self._guard._remote, "enabled", False):
            self._guard._sync_remote_session()
        return self

    def close(self) -> None:
        if self._guard is not None:
            self._guard.close()

    def _build_guard(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
        environment: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentGuard:
        guard = AgentGuard(
            session_id=session_id,
            user_id=user_id if user_id is not None else self._config["user_id"],
            agent_id=agent_id if agent_id is not None else self._config["agent_id"],
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
        )
        if metadata:
            guard.context.metadata.update(metadata)
        return guard

    def _require_guard(self) -> AgentGuard:
        if self._guard is None:
            raise RuntimeError("guard session not started; call start(principal=...) first")
        return self._guard

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_guard(), name)
