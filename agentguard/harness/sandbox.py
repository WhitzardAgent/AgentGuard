"""Client-side execution sandbox.

Two layers of protection:

1. **Capability gate** — a tool may only exercise capabilities explicitly
   granted to the sandbox; anything else raises :class:`SandboxViolation`
   *before* the callable runs, so unsafe access never happens.
2. **Execution backend** — once authorized, the callable is run through a
   pluggable :class:`~agentguard.harness.sandbox_backends.SandboxBackend`
   (``local`` / ``subprocess`` / ``opensandbox``) providing increasing
   isolation strength.

This keeps the policy boundary enforced on the client while letting deployments
opt into real process/container isolation (e.g. OpenSandbox) for shell and code
execution.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from agentguard.harness.sandbox_backends import SandboxBackend, build_backend
from agentguard.tools.capability import Capability

log = logging.getLogger("agentguard.harness")


class SandboxViolation(RuntimeError):
    """Raised when execution requests a capability the sandbox did not grant."""

    def __init__(self, capability: str, tool_name: str | None = None) -> None:
        self.capability = capability
        self.tool_name = tool_name
        super().__init__(
            f"sandbox denied capability '{capability}'"
            + (f" for tool '{tool_name}'" if tool_name else "")
        )


class Sandbox:
    def __init__(
        self,
        *,
        enabled: bool = True,
        allowed_capabilities: Iterable[str | Capability] | None = None,
        strict: bool = False,
        backend: "str | SandboxBackend | None" = None,
        **backend_options: Any,
    ) -> None:
        self.enabled = enabled
        self.strict = strict
        self.backend: SandboxBackend = build_backend(backend, **backend_options)
        # When None, all capabilities are permitted (sandbox observes only).
        self._allowed: set[str] | None = (
            None
            if allowed_capabilities is None
            else {
                c.value if isinstance(c, Capability) else str(c)
                for c in allowed_capabilities
            }
        )

    def allow(self, *capabilities: str | Capability) -> None:
        if self._allowed is None:
            self._allowed = set()
        for cap in capabilities:
            self._allowed.add(cap.value if isinstance(cap, Capability) else str(cap))

    def check(self, capabilities: Iterable[str], *, tool_name: str | None = None) -> None:
        if not self.enabled or self._allowed is None:
            return
        for cap in capabilities:
            if cap in (Capability.NONE.value, ""):
                continue
            if cap not in self._allowed:
                raise SandboxViolation(cap, tool_name)

    def run(
        self,
        fn: Callable[..., Any],
        *,
        args: dict[str, Any],
        capabilities: Iterable[str],
        tool_name: str | None = None,
    ) -> Any:
        """Execute ``fn(**args)`` after verifying its capabilities are granted.

        Authorized execution is delegated to the configured backend, which may
        run it in-process, in a restricted subprocess, or inside an OpenSandbox
        instance depending on configuration.
        """
        caps = list(capabilities)
        self.check(caps, tool_name=tool_name)
        if not self.enabled:
            return fn(**args)
        if self.strict:
            log.debug(
                "sandbox(strict, backend=%s) executing %s caps=%s",
                self.backend.name, tool_name, caps,
            )
        return self.backend.execute(
            fn, args=dict(args), capabilities=caps, tool_name=tool_name
        )

    def close(self) -> None:
        self.backend.close()
