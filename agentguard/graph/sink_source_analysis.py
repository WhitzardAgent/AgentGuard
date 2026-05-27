"""Standalone source-sink analysis helpers for the execution graph."""

from __future__ import annotations

from typing import Iterable

from agentguard.storage.graph_store import GraphReadAPI


def has_sensitive_path(
    graph: GraphReadAPI,
    sink_call_id: str,
    source_labels: Iterable[str],
    max_hops: int = 6,
) -> bool:
    """Check if there is a tainted data path from a sensitive source to the given sink."""
    return graph.exists_path_to_sink(
        sink_call_id=sink_call_id,
        source_labels=source_labels,
        max_hops=max_hops,
    )
