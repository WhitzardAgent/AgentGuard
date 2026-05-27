"""Async graph writer. Buffers events and flushes in the background."""

from __future__ import annotations

import queue
import threading
from typing import Any

from agentguard.storage.graph_store import GraphWriteAPI
from agentguard.storage.session_store import StateCache, CACHE_KEYS
from agentguard.graph.model import EdgeType, NodeType
from agentguard.models.decisions import Decision
from agentguard.models.events import EventType, RuntimeEvent


class GraphWriter:
    """Non-blocking writer. submit() is O(1); actual persistence happens on a worker thread."""

    _SENTINEL: object = object()

    def __init__(
        self,
        store: GraphWriteAPI,
        cache: StateCache,
        *,
        queue_size: int = 4096,
    ) -> None:
        self._store = store
        self._cache = cache
        self._q: "queue.Queue[object]" = queue.Queue(maxsize=queue_size)
        self._stopped = threading.Event()
        self._worker = threading.Thread(target=self._run, name="agentguard-graph-writer",
                                        daemon=True)
        self._worker.start()

    def submit(self, event: RuntimeEvent, decision: Decision | None = None) -> None:
        try:
            self._q.put_nowait((event, decision))
        except queue.Full:
            pass

    def close(self, timeout: float = 2.0) -> None:
        self._q.put(self._SENTINEL)
        self._stopped.set()
        self._worker.join(timeout=timeout)

    def flush(self, timeout: float = 1.0) -> None:
        self._q.join()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is self._SENTINEL:
                    return
                event, decision = item  # type: ignore[misc]
                self._write(event, decision)
            except Exception:
                pass
            finally:
                self._q.task_done()

    def _write(self, event: RuntimeEvent, decision: Decision | None) -> None:
        p = event.principal
        self._store.upsert_node(
            NodeType.AGENT, p.agent_id,
            {
                "role": p.role,
                "trust_level": p.trust_level,
                "parent_id": p.parent_agent_id,
                "user_id": p.user_id,
            },
        )
        if p.parent_agent_id:
            self._store.upsert_edge(
                EdgeType.SPAWNED,
                NodeType.AGENT, p.parent_agent_id,
                NodeType.AGENT, p.agent_id,
            )

        if event.event_type in (EventType.TOOL_CALL_ATTEMPT,
                                EventType.TOOL_CALL_REQUESTED) and event.tool_call is not None:
            self._write_tool_call(event, decision)
        elif event.event_type in (EventType.TOOL_CALL_RESULT,
                                  EventType.TOOL_CALL_COMPLETED) and event.tool_call is not None:
            self._store.upsert_node(
                NodeType.TOOL_CALL, event.event_id,
                {"tool_name": event.tool_call.tool_name,
                 "ts_ms": event.ts_ms,
                 "action": "result",
                 "risk": decision.risk_score if decision else 0.0},
            )

    def _write_tool_call(self, event: RuntimeEvent, decision: Decision | None) -> None:
        tc = event.tool_call
        assert tc is not None
        p = event.principal
        action = decision.action.value if decision else "allow"
        risk = decision.risk_score if decision else 0.0

        self._store.upsert_node(
            NodeType.TOOL_CALL, event.event_id,
            {
                "tool_name": tc.tool_name,
                "ts_ms": event.ts_ms,
                "action": action,
                "risk": risk,
                "sink_type": tc.sink_type,
            },
        )
        self._store.upsert_edge(
            EdgeType.INVOKED,
            NodeType.AGENT, p.agent_id,
            NodeType.TOOL_CALL, event.event_id,
        )
        for ref in event.provenance_refs:
            self._store.upsert_node(
                NodeType.RESOURCE, ref.node_id,
                {"labels": [ref.label], "kind": "derived"},
            )
            self._store.upsert_edge(
                EdgeType.READ_FROM,
                NodeType.TOOL_CALL, event.event_id,
                NodeType.RESOURCE, ref.node_id,
            )
            self._cache.sadd(CACHE_KEYS.labels(p.session_id), ref.label)
            # If the resource was produced by a prior tool call, build a
            # DERIVED_FROM edge: current_call → parent_call  (data flow)
            if ref.parent_tool_call_id:
                self._store.upsert_edge(
                    EdgeType.DERIVED_FROM,
                    NodeType.TOOL_CALL, event.event_id,
                    NodeType.TOOL_CALL, ref.parent_tool_call_id,
                )

        self._cache.lpush_capped(CACHE_KEYS.recent_tools(p.session_id), tc.tool_name)
        # Note: trace_log is appended synchronously in Pipeline.handle_attempt
        # so that the next call's trace() predicate sees this entry without
        # waiting for the async graph writer to flush.

        if event.goal:
            goal_id = f"{p.session_id}:goal"
            self._store.upsert_node(
                NodeType.GOAL, goal_id,
                {"text": event.goal, "session_id": p.session_id},
            )
            self._store.upsert_edge(
                EdgeType.UNDER_GOAL,
                NodeType.TOOL_CALL, event.event_id,
                NodeType.GOAL, goal_id,
            )
