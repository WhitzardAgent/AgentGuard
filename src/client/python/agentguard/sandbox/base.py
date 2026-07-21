"""Sandbox backend interface."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentguard.schemas.sandbox import SandboxResult


class BaseSandbox:
    """Execution boundary for tool callables."""

    name: str = "base"

    def execute(
        self,
        fn: Callable[..., Any],
        arguments: dict[str, Any],
        *,
        capabilities: list[str] | None = None,
        tool_name: str | None = None,
    ) -> SandboxResult:
        raise NotImplementedError
