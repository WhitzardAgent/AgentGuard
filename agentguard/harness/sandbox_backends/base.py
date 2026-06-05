"""Sandbox backend protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class SandboxBackend(ABC):
    """Executes an authorized tool callable inside an isolation boundary."""

    name: str = "backend"

    @abstractmethod
    def execute(
        self,
        fn: Callable[..., Any],
        *,
        args: dict[str, Any],
        capabilities: list[str],
        tool_name: str | None = None,
    ) -> Any:
        """Run ``fn(**args)`` and return its result.

        ``capabilities`` is the already-authorized capability set (the caller's
        capability gate runs *before* this method). Implementations may use it
        to decide *how* to isolate (e.g. only shell/exec needs a real sandbox).
        """
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - optional cleanup hook
        """Release any backend resources (sandbox instances, pools, …)."""
        return None
