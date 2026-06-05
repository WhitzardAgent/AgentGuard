"""Local, in-process policy evaluation against a PolicySnapshot."""

from __future__ import annotations

from agentguard.pep.policy_snapshot import PolicySnapshot
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision
from agentguard.schemas.events import RuntimeEvent


class LocalEvaluator:
    """Evaluates events with the local rule matcher held in a snapshot."""

    def __init__(self, snapshot: PolicySnapshot) -> None:
        self._snapshot = snapshot

    @property
    def snapshot(self) -> PolicySnapshot:
        return self._snapshot

    def set_snapshot(self, snapshot: PolicySnapshot) -> None:
        self._snapshot = snapshot

    def evaluate(self, event: RuntimeEvent, context: RuntimeContext) -> Decision:
        return self._snapshot.matcher.evaluate(event, context)
