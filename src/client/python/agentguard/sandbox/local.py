"""Local permission sandbox: enforce a profile, then run in-process."""
from __future__ import annotations

import time
from typing import Any, Callable

from agentguard.sandbox.base import BaseSandbox
from agentguard.sandbox.permissions import check_permissions
from agentguard.sandbox.profiles import PermissionProfile
from agentguard.schemas.sandbox import SandboxResult


class LocalPermissionSandbox(BaseSandbox):
    name = "local"

    def __init__(self, profile: PermissionProfile | None = None) -> None:
        self.profile = profile or PermissionProfile.restricted()

    def execute(
        self,
        fn: Callable[..., Any],
        arguments: dict[str, Any],
        *,
        capabilities: list[str] | None = None,
        tool_name: str | None = None,
    ) -> SandboxResult:
        check = check_permissions(self.profile, capabilities or [], arguments)
        if not check.allowed:
            return SandboxResult.fail(
                f"permission denied: {check.reason}",
                backend=self.name,
                metadata={"capabilities": capabilities or []},
            )
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
