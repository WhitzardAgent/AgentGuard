"""Permission profiles describing sandbox boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PermissionProfile:
    """Declarative permission boundary for a sandbox."""

    allowed_file_roots: list[str] = field(default_factory=list)
    denied_file_roots: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)
    allowed_env_vars: list[str] = field(default_factory=list)
    allow_subprocess: bool = False
    allow_network: bool = False
    allow_write: bool = False
    timeout_s: float = 10.0
    memory_limit_mb: int | None = None

    @staticmethod
    def permissive() -> "PermissionProfile":
        return PermissionProfile(
            allow_subprocess=True, allow_network=True, allow_write=True, timeout_s=30.0
        )

    @staticmethod
    def restricted() -> "PermissionProfile":
        return PermissionProfile(
            allow_subprocess=False, allow_network=False, allow_write=False, timeout_s=5.0
        )
