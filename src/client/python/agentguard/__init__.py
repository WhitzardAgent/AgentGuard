"""AgentGuard client package."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentguard.guard import AgentGuard

__all__ = ["AgentGuard"]
__version__ = "0.3.0"


def __getattr__(name: str) -> Any:
    if name == "AgentGuard":
        from agentguard.guard import AgentGuard

        return AgentGuard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
