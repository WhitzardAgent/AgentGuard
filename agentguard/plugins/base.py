"""Plugin protocol for optional AgentGuard capabilities."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentguard.sdk.guard import Guard


class AgentGuardPlugin(Protocol):
    """Optional extension loaded by :class:`agentguard.Guard`.

    Core AgentGuard stays functional without plugins. Plugins may attach hooks,
    decision resolvers, adapters, or background reviewers when explicitly used.
    """

    def setup(self, guard: "Guard") -> None: ...

    def teardown(self) -> None: ...


__all__ = ["AgentGuardPlugin"]
