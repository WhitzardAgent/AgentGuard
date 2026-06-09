"""Sandbox executor: choose a backend by config and run all tool calls."""
from __future__ import annotations

from typing import Any, Callable

from agentguard.sandbox.base import BaseSandbox
from agentguard.sandbox.local import LocalPermissionSandbox
from agentguard.sandbox.noop import NoopSandbox
from agentguard.sandbox.profiles import PermissionProfile
from agentguard.sandbox.subprocess import SubprocessSandbox
from agentguard.schemas.sandbox import SandboxResult

_BACKENDS = {
    "noop": NoopSandbox,
    "local": LocalPermissionSandbox,
    "subprocess": SubprocessSandbox,
}


def build_sandbox(
    backend: str | BaseSandbox = "local",
    profile: PermissionProfile | None = None,
) -> BaseSandbox:
    if isinstance(backend, BaseSandbox):
        return backend
    cls = _BACKENDS.get(backend)
    if cls is None:
        raise ValueError(f"unknown sandbox backend: {backend}")
    if cls is NoopSandbox:
        return cls()
    return cls(profile)  # type: ignore[call-arg]


class SandboxExecutor:
    """Single entry point through which all tool execution must pass."""

    def __init__(
        self,
        backend: str | BaseSandbox = "local",
        profile: PermissionProfile | None = None,
    ) -> None:
        self.backend = build_sandbox(backend, profile)

    def run(
        self,
        fn: Callable[..., Any],
        arguments: dict[str, Any],
        *,
        capabilities: list[str] | None = None,
        tool_name: str | None = None,
    ) -> SandboxResult:
        return self.backend.execute(
            fn, arguments, capabilities=capabilities, tool_name=tool_name
        )
