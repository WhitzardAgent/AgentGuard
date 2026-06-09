"""Build a simple event graph from a trace."""
from __future__ import annotations

from typing import Any


def build_event_graph(events: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = [{"id": e.get("event_id"), "type": e.get("event_type")} for e in events]
    edges = [
        {"from": events[i].get("event_id"), "to": events[i + 1].get("event_id")}
        for i in range(len(events) - 1)
    ]
    return {"nodes": nodes, "edges": edges}


__all__ = ["build_event_graph"]
