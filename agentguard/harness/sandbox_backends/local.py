"""In-process backend — the default, fastest execution path."""

from __future__ import annotations

from typing import Any, Callable

from agentguard.harness.sandbox_backends.base import SandboxBackend


class LocalBackend(SandboxBackend):
    name = "local"

    def execute(
        self,
        fn: Callable[..., Any],
        *,
        args: dict[str, Any],
        capabilities: list[str],
        tool_name: str | None = None,
    ) -> Any:
        return fn(**args)
