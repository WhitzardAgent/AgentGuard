"""OpenSandbox backend — offloads shell/code execution to OpenSandbox.

`OpenSandbox <https://github.com/alibaba/OpenSandbox>`_ is Alibaba's open-source,
production-grade sandbox runtime for AI agents (Docker/Kubernetes). When a tool
exercises a ``shell``/``exec`` capability and carries a command (or ``code``),
this backend runs it *inside* an isolated OpenSandbox instance instead of on the
host — so even an allowed ``ls`` or build command never touches the host FS.

The integration is fully optional and lazy:

* ``pip install opensandbox`` (+ a reachable control plane) enables real
  isolation;
* otherwise the backend logs once and falls back to the configured local
  backend, keeping the Harness runnable everywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agentguard.harness.sandbox_backends.base import SandboxBackend
from agentguard.harness.sandbox_backends.local import LocalBackend

log = logging.getLogger("agentguard.harness")

_DEFAULT_COMMAND_ARGS = ("command", "cmd", "shell", "script")
_DEFAULT_CODE_ARGS = ("code", "source", "snippet")


class OpenSandboxBackend(SandboxBackend):
    name = "opensandbox"

    def __init__(
        self,
        *,
        image: str = "opensandbox/code-interpreter:latest",
        domain: str | None = None,
        api_key: str | None = None,
        language: str = "python",
        command_arg_names: tuple[str, ...] = _DEFAULT_COMMAND_ARGS,
        code_arg_names: tuple[str, ...] = _DEFAULT_CODE_ARGS,
        fallback: SandboxBackend | None = None,
        run_only_capabilities: tuple[str, ...] = ("shell", "exec"),
    ) -> None:
        self.image = image
        self.domain = domain
        self.api_key = api_key
        self.language = language
        self.command_arg_names = command_arg_names
        self.code_arg_names = code_arg_names
        self.run_only_capabilities = run_only_capabilities
        self._fallback = fallback or LocalBackend()
        self._sandbox: Any = None
        self._unavailable = False

    # ── lazy connection ─────────────────────────────────────────────────
    def _ensure_sandbox(self) -> Any:
        if self._sandbox is not None or self._unavailable:
            return self._sandbox
        try:
            from opensandbox.sandbox import SandboxSync  # type: ignore
            from opensandbox.config import ConnectionConfigSync  # type: ignore
        except Exception as exc:  # SDK not installed
            log.warning("OpenSandbox SDK unavailable (%s); using fallback backend", exc)
            self._unavailable = True
            return None
        try:
            config = None
            if self.domain:
                config = ConnectionConfigSync(domain=self.domain, api_key=self.api_key or "")
            self._sandbox = (
                SandboxSync.create(self.image, connection_config=config)
                if config is not None
                else SandboxSync.create(self.image)
            )
        except Exception as exc:  # control plane unreachable
            log.warning("OpenSandbox connect failed (%s); using fallback backend", exc)
            self._unavailable = True
            self._sandbox = None
        return self._sandbox

    # ── execution ───────────────────────────────────────────────────────
    def execute(
        self,
        fn: Callable[..., Any],
        *,
        args: dict[str, Any],
        capabilities: list[str],
        tool_name: str | None = None,
    ) -> Any:
        needs_isolation = bool(set(capabilities) & set(self.run_only_capabilities))
        command = self._extract(args, self.command_arg_names)
        code = self._extract(args, self.code_arg_names)

        if not needs_isolation or (command is None and code is None):
            # Nothing shell/code-shaped to offload → run via fallback backend.
            return self._fallback.execute(
                fn, args=args, capabilities=capabilities, tool_name=tool_name
            )

        sandbox = self._ensure_sandbox()
        if sandbox is None:
            return self._fallback.execute(
                fn, args=args, capabilities=capabilities, tool_name=tool_name
            )

        try:
            if command is not None:
                return self._run_command(sandbox, str(command))
            return self._run_code(sandbox, str(code))
        except Exception as exc:  # noqa: BLE001 - never crash the call path
            log.warning("OpenSandbox execution failed (%s); using fallback", exc)
            return self._fallback.execute(
                fn, args=args, capabilities=capabilities, tool_name=tool_name
            )

    def _run_command(self, sandbox: Any, command: str) -> str:
        execution = sandbox.commands.run(command)
        return self._stdout(execution)

    def _run_code(self, sandbox: Any, code: str) -> str:
        interpreter = getattr(sandbox, "run_code", None) or getattr(sandbox, "code", None)
        if interpreter is None:
            execution = sandbox.commands.run(code)
        else:
            execution = (
                interpreter(code, language=self.language)
                if callable(interpreter)
                else interpreter.run(code)
            )
        return self._stdout(execution)

    @staticmethod
    def _stdout(execution: Any) -> str:
        try:
            logs = execution.logs.stdout
            return "".join(getattr(line, "text", str(line)) for line in logs)
        except Exception:
            return str(getattr(execution, "text", execution))

    @staticmethod
    def _extract(args: dict[str, Any], names: tuple[str, ...]) -> Any:
        for name in names:
            if name in args and args[name]:
                return args[name]
        return None

    def close(self) -> None:
        if self._sandbox is not None:
            for method in ("kill", "close", "stop"):
                fn = getattr(self._sandbox, method, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:  # pragma: no cover
                        pass
                    break
            self._sandbox = None
