"""GraphActor: execution graph maintenance (Instruction.md §3.4).

Forwards every event to the asynchronous :class:`GraphWriter` worker
thread, which builds the execution graph (Agent → ToolCall →
DERIVED_FROM edges → Resource).
"""

from __future__ import annotations

from agentguard.graph.builder import GraphWriter
from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.event_bus import EventBus, Message


class GraphActor(BaseActor):
    actor_name = "graph"

    def __init__(self, bus: EventBus, writer: GraphWriter) -> None:
        super().__init__(bus)
        self._writer = writer

    async def handle(self, msg: Message) -> None:
        if msg.topic != "graph_update":
            return
        if not isinstance(msg.payload, dict):
            return
        event: RuntimeEvent | None = msg.payload.get("event")
        decision: Decision | None = msg.payload.get("decision")
        if event is None:
            return
        self._writer.submit(event, decision)

    async def on_start(self) -> None:
        self.bus.subscribe("graph_update", self.receive)

    async def on_stop(self) -> None:
        self.bus.unsubscribe("graph_update", self.receive)
        self._writer.close()
