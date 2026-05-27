"""Execution security graph storage.

Default backend is a process-local in-memory adjacency structure so the
framework boots with zero external dependencies.
"""

from __future__ import annotations

import abc
import threading
from collections import defaultdict
from typing import Any, Iterable

from agentguard.graph.model import EdgeType, NodeType


class GraphReadAPI(abc.ABC):
    @abc.abstractmethod
    def exists_path_to_sink(
        self,
        sink_call_id: str,
        source_labels: Iterable[str],
        max_hops: int = 6,
    ) -> bool: ...

    @abc.abstractmethod
    def resource_labels(self, resource_id: str) -> set[str]: ...

    @abc.abstractmethod
    def agent_ancestors(self, agent_id: str) -> list[str]: ...


class GraphWriteAPI(abc.ABC):
    @abc.abstractmethod
    def upsert_node(self, ntype: NodeType, node_id: str, props: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def upsert_edge(
        self,
        etype: EdgeType,
        src_type: NodeType,
        src_id: str,
        dst_type: NodeType,
        dst_id: str,
        props: dict[str, Any] | None = None,
    ) -> None: ...


class InMemoryGraphStore(GraphReadAPI, GraphWriteAPI):
    """Reference implementation. Not intended for production scale."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[NodeType, str], dict[str, Any]] = {}
        self._out: dict[tuple[NodeType, str],
                        list[tuple[EdgeType, NodeType, str, dict[str, Any]]]] = defaultdict(list)
        self._lock = threading.RLock()

    # ------------------------------ writes ------------------------------
    def upsert_node(self, ntype: NodeType, node_id: str, props: dict[str, Any]) -> None:
        with self._lock:
            key = (ntype, node_id)
            existing = self._nodes.get(key, {})
            for k, v in props.items():
                if k == "labels" and isinstance(v, (list, set)):
                    # Merge labels rather than overwrite — prevents losing earlier tags
                    old = existing.get("labels") or []
                    merged: set[str] = set(old) | set(v)
                    existing["labels"] = list(merged)
                else:
                    existing[k] = v
            self._nodes[key] = existing

    def upsert_edge(
        self,
        etype: EdgeType,
        src_type: NodeType,
        src_id: str,
        dst_type: NodeType,
        dst_id: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._out[(src_type, src_id)].append((etype, dst_type, dst_id, props or {}))

    # ------------------------------ reads ------------------------------
    def resource_labels(self, resource_id: str) -> set[str]:
        with self._lock:
            node = self._nodes.get((NodeType.RESOURCE, resource_id))
            if not node:
                return set()
            return set(node.get("labels", []))

    def agent_ancestors(self, agent_id: str) -> list[str]:
        out: list[str] = []
        with self._lock:
            cur = agent_id
            seen: set[str] = set()
            while cur and cur not in seen:
                seen.add(cur)
                node = self._nodes.get((NodeType.AGENT, cur))
                if not node:
                    break
                parent = node.get("parent_id")
                if not parent:
                    break
                out.append(parent)
                cur = parent
        return out

    def exists_path_to_sink(
        self,
        sink_call_id: str,
        source_labels: Iterable[str],
        max_hops: int = 6,
    ) -> bool:
        """Follow outgoing DERIVED_FROM / READ_FROM edges from the sink call
        to discover whether any upstream Resource carries a matching label."""
        label_patterns = [self._normalize(lbl) for lbl in source_labels]
        if not label_patterns:
            return False

        with self._lock:
            frontier: list[tuple[NodeType, str]] = [(NodeType.TOOL_CALL, sink_call_id)]
            visited: set[tuple[NodeType, str]] = set()

            for _ in range(max_hops):
                next_frontier: list[tuple[NodeType, str]] = []
                for node_key in frontier:
                    if node_key in visited:
                        continue
                    visited.add(node_key)
                    for etype, dst_type, dst_id, _props in self._out.get(node_key, []):
                        if etype not in (EdgeType.DERIVED_FROM, EdgeType.READ_FROM):
                            continue
                        dst_key = (dst_type, dst_id)
                        if dst_type is NodeType.RESOURCE:
                            labels = self._nodes.get(dst_key, {}).get("labels", [])
                            if any(self._label_match(pat, lbl)
                                   for pat in label_patterns for lbl in labels):
                                return True
                        next_frontier.append(dst_key)
                frontier = next_frontier
                if not frontier:
                    break
        return False

    def _reverse_index(self) -> dict[tuple[NodeType, str],
                                     list[tuple[EdgeType, NodeType, str]]]:
        idx: dict[tuple[NodeType, str], list[tuple[EdgeType, NodeType, str]]] = defaultdict(list)
        for (src_type, src_id), edges in self._out.items():
            for etype, dst_type, dst_id, _props in edges:
                idx[(dst_type, dst_id)].append((etype, src_type, src_id))
        return idx

    @staticmethod
    def _normalize(pattern: str) -> tuple[str, bool]:
        if pattern.endswith("/*"):
            return pattern[:-2], True
        if pattern.endswith("*"):
            return pattern[:-1], True
        return pattern, False

    @staticmethod
    def _label_match(pattern: tuple[str, bool], label: str) -> bool:
        prefix, is_prefix = pattern
        if is_prefix:
            return label == prefix or label.startswith(prefix + "/") or label.startswith(prefix + ".")
        return label == prefix
