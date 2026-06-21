"""Subprocess sandbox: run a callable in an isolated process with limits."""
from __future__ import annotations

import io
import multiprocessing as mp
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable

from agentguard.sandbox.base import BaseSandbox
from agentguard.sandbox.profiles import PermissionProfile
from agentguard.schemas.sandbox import SandboxResult


def _worker(
    fn: Callable[..., Any],
    arguments: dict[str, Any],
    env: dict[str, str] | None,
    cwd: str | None,
    memory_limit_mb: int | None,
    conn: Any,
) -> None:
    # Apply env allowlist.
    if env is not None:
        os.environ.clear()
        os.environ.update(env)
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            pass
    # Best-effort resource limit (POSIX only).
    if memory_limit_mb:
        try:
            import resource  # local import; not available on Windows

            limit = memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except Exception:
            pass

    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            value = fn(**arguments)
        conn.send({"success": True, "value": value, "stdout": out.getvalue(), "stderr": err.getvalue()})
    except Exception as exc:
        conn.send(
            {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout": out.getvalue(),
                "stderr": err.getvalue(),
            }
        )
    finally:
        conn.close()


class SubprocessSandbox(BaseSandbox):
    name = "subprocess"

    def __init__(
        self,
        profile: PermissionProfile | None = None,
        *,
        cwd: str | None = None,
        env_allowlist: list[str] | None = None,
    ) -> None:
        self.profile = profile or PermissionProfile.restricted()
        self.cwd = cwd
        self.env_allowlist = env_allowlist

    def _child_env(self) -> dict[str, str] | None:
        allow = self.env_allowlist if self.env_allowlist is not None else self.profile.allowed_env_vars
        if allow is None:
            return None
        return {k: os.environ[k] for k in allow if k in os.environ}

    def execute(
        self,
        fn: Callable[..., Any],
        arguments: dict[str, Any],
        *,
        capabilities: list[str] | None = None,
        tool_name: str | None = None,
    ) -> SandboxResult:
        start = time.time()
        ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_worker,
            args=(fn, arguments, self._child_env(), self.cwd, self.profile.memory_limit_mb, child_conn),
        )
        proc.start()
        child_conn.close()
        timeout = self.profile.timeout_s
        proc.join(timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(1.0)
            return SandboxResult.fail(
                f"sandbox timeout after {timeout}s",
                backend=self.name,
                duration_ms=(time.time() - start) * 1000,
                metadata={"timeout": True},
            )

        try:
            payload = parent_conn.recv() if parent_conn.poll() else None
        except EOFError:
            payload = None
        duration = (time.time() - start) * 1000
        if not payload:
            return SandboxResult.fail(
                "sandbox produced no result", backend=self.name, duration_ms=duration
            )
        if payload.get("success"):
            return SandboxResult.ok(
                payload.get("value"),
                backend=self.name,
                stdout=payload.get("stdout", ""),
                stderr=payload.get("stderr", ""),
                duration_ms=duration,
            )
        return SandboxResult.fail(
            payload.get("error", "unknown error"),
            backend=self.name,
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
            duration_ms=duration,
        )
