"""Sandbox execution schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxResult:
    """Structured result of a sandboxed execution."""

    success: bool
    value: Any = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    backend: str = "noop"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "value": self.value,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "backend": self.backend,
            "metadata": self.metadata,
        }

    @staticmethod
    def ok(value: Any, **kw: Any) -> "SandboxResult":
        return SandboxResult(success=True, value=value, **kw)

    @staticmethod
    def fail(error: str, **kw: Any) -> "SandboxResult":
        return SandboxResult(success=False, error=error, **kw)
