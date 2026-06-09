"""No-op sandbox: runs the tool directly (observe-only boundary)."""
from __future__ import annotations

import time
from typing import Any, Callable

from agentguard.sandbox.base import BaseSandbox
from agentguard.schemas.sandbox import SandboxResult


class NoopSandbox(BaseSandbox):
    name = "noop"

    def execute(
        self,
        fn: Callable[..., Any],
        arguments: dict[str, Any],
        *,
        capabilities: list[str] | None = None,
        tool_name: str | None = None,
    ) -> SandboxResult:
        start = time.time()
        try:
            value = fn(**arguments)
        except Exception as exc:
            return SandboxResult.fail(
                str(exc), backend=self.name, duration_ms=(time.time() - start) * 1000
            )
        return SandboxResult.ok(
            value, backend=self.name, duration_ms=(time.time() - start) * 1000
        )
