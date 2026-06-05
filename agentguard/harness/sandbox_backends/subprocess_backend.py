"""Subprocess backend — runs a tool in a separate, restricted Python process.

Provides real address-space isolation and CPU/memory/time limits using only the
standard library (``multiprocessing`` + ``resource``). It is a pragmatic
middle-ground between in-process execution and a full container sandbox.

If the target callable cannot be pickled (e.g. a closure/lambda) or the platform
cannot spawn a worker, it transparently falls back to in-process execution and
logs a warning, so correctness is never sacrificed for isolation.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
from typing import Any, Callable

from agentguard.harness.sandbox_backends.base import SandboxBackend

log = logging.getLogger("agentguard.harness")


def _limit_resources(cpu_seconds: int, memory_mb: int) -> None:
    try:
        import resource

        if cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        if memory_mb > 0:
            soft = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (soft, soft))
    except Exception:  # pragma: no cover - platform dependent
        pass


def _worker(
    queue: "mp.Queue[Any]",
    fn: Callable[..., Any],
    args: dict[str, Any],
    cpu_seconds: int,
    memory_mb: int,
) -> None:  # pragma: no cover - runs in a child process
    _limit_resources(cpu_seconds, memory_mb)
    try:
        queue.put(("ok", fn(**args)))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("err", f"{type(exc).__name__}: {exc}"))


class SubprocessExecutionError(RuntimeError):
    pass


class SubprocessBackend(SandboxBackend):
    name = "subprocess"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        cpu_seconds: int = 10,
        memory_mb: int = 512,
        start_method: str = "spawn",
    ) -> None:
        self.timeout = timeout
        self.cpu_seconds = cpu_seconds
        self.memory_mb = memory_mb
        try:
            self._ctx = mp.get_context(start_method)
        except ValueError:  # pragma: no cover
            self._ctx = mp.get_context()

    def execute(
        self,
        fn: Callable[..., Any],
        *,
        args: dict[str, Any],
        capabilities: list[str],
        tool_name: str | None = None,
    ) -> Any:
        queue: "mp.Queue[Any]" = self._ctx.Queue()
        try:
            proc = self._ctx.Process(
                target=_worker,
                args=(queue, fn, dict(args), self.cpu_seconds, self.memory_mb),
            )
            proc.start()
        except Exception as exc:  # pickling / spawn failure → graceful fallback
            log.warning(
                "subprocess sandbox cannot isolate %s (%s); running in-process",
                tool_name, exc,
            )
            return fn(**args)

        proc.join(self.timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(1.0)
            raise SubprocessExecutionError(
                f"tool '{tool_name}' exceeded sandbox timeout of {self.timeout}s"
            )
        if queue.empty():
            raise SubprocessExecutionError(
                f"tool '{tool_name}' produced no result (exit code {proc.exitcode})"
            )
        status, payload = queue.get()
        if status == "err":
            raise SubprocessExecutionError(f"tool '{tool_name}' failed in sandbox: {payload}")
        return payload
