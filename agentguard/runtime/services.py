"""Pipeline service contracts.

The runtime is composed from four narrow services that the synchronous
:class:`agentguard.runtime.dispatcher.Pipeline` orchestrates:

* :class:`PolicyService`   - decide ALLOW / DENY / DEGRADE / *_CHECK for an event.
* :class:`EnforcerService` - apply the decision and execute the underlying tool.
* :class:`GraphService`    - persist execution-graph edges (async writer).
* :class:`AuditService`    - record event + decision pairs.

In v1 every concrete implementation lives in this process, but the
abstractions deliberately mirror what an out-of-process / RPC backend would
expose so future deployments can swap any service for a remote one without
touching the orchestration layer.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.review.tickets import ApprovalBridge


@runtime_checkable
class PolicyService(Protocol):
    """Hot-path rule evaluation."""

    def evaluate(
        self, event: RuntimeEvent, features: dict[str, Any]
    ) -> Decision: ...

    def rules_for_agent(self, agent_id: str) -> list[CompiledRule]: ...


@runtime_checkable
class EnforcerService(Protocol):
    """Apply a decision to a real tool invocation."""

    def apply(
        self,
        event: RuntimeEvent,
        decision: Decision,
        original_executor: Callable[[RuntimeEvent], Any],
        *,
        revalidate: Callable[[RuntimeEvent], Decision] | None = None,
    ) -> Any: ...

    def resolve_remote_decision(
        self,
        event: RuntimeEvent,
        decision: Decision,
    ) -> Decision: ...

    def approval_bridge(self) -> ApprovalBridge: ...


@runtime_checkable
class GraphService(Protocol):
    """Async / queued execution-graph writer."""

    def submit(
        self, event: RuntimeEvent, decision: Decision | None = None
    ) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class AuditService(Protocol):
    """Append-only audit recorder."""

    def log(
        self, event: RuntimeEvent, decision: Decision | None = None
    ) -> None: ...

    def recent(self, n: int = 100) -> list[dict[str, Any]]: ...


__all__ = [
    "AuditService",
    "EnforcerService",
    "GraphService",
    "PolicyService",
]
