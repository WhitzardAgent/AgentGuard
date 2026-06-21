"""AgentGuard client package."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentguard.guard import AgentGuard
    from agentguard.compat import Guard, Principal

__all__ = ["AgentGuard", "Guard", "Principal"]
__version__ = "0.3.0"


def __getattr__(name: str) -> Any:
    if name == "AgentGuard":
        from agentguard.guard import AgentGuard

        return AgentGuard
    if name in {"Guard", "Principal"}:
        from agentguard.compat import Guard, Principal

        exports = {"Guard": Guard, "Principal": Principal}
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
