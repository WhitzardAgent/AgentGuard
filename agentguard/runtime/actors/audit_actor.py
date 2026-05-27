"""AuditActor: audit logging (Instruction.md §3.8).

Persists every (event, decision) pair into the :class:`AuditLogWriter`
ring buffer. The asynchronous :class:`AuditLoop` is responsible for
draining this buffer to a configured persistent sink.
"""

from __future__ import annotations

from agentguard.audit.logger import AuditLogWriter
from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.event_bus import EventBus, Message


class AuditActor(BaseActor):
    actor_name = "audit"

    def __init__(self, bus: EventBus, audit_writer: AuditLogWriter) -> None:
        super().__init__(bus)
        self._writer = audit_writer

    async def handle(self, msg: Message) -> None:
        if msg.topic != "audit_event":
            return
        if not isinstance(msg.payload, dict):
            return
        event: RuntimeEvent | None = msg.payload.get("event")
        decision: Decision | None = msg.payload.get("decision")
        if event is None:
            return
        self._writer.log(event, decision)

    async def on_start(self) -> None:
        self.bus.subscribe("audit_event", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("audit_event", self.receive)
