"""Tests for storage backends."""

from agentguard.storage.session_store import InMemoryStateCache
from agentguard.storage.graph_store import InMemoryGraphStore
from agentguard.storage.event_store import LRUCache
from agentguard.graph.model import NodeType, EdgeType


def test_state_cache_kv():
    c = InMemoryStateCache()
    c.set("k1", "v1")
    assert c.get("k1") == "v1"
    assert c.get("missing") is None


def test_state_cache_set():
    c = InMemoryStateCache()
    c.sadd("s1", "a", "b")
    c.sadd("s1", "b", "c")
    assert c.smembers("s1") == {"a", "b", "c"}


def test_state_cache_list():
    c = InMemoryStateCache()
    c.lpush_capped("l1", "first")
    c.lpush_capped("l1", "second")
    items = c.lrange("l1", 0, -1)
    assert items == ["second", "first"]


def test_graph_store_node():
    g = InMemoryGraphStore()
    g.upsert_node(NodeType.AGENT, "a1", {"role": "admin"})
    labels = g.resource_labels("a1")
    assert isinstance(labels, set)


def test_graph_store_edge():
    g = InMemoryGraphStore()
    g.upsert_node(NodeType.AGENT, "a1", {"role": "admin"})
    g.upsert_node(NodeType.TOOL_CALL, "tc1", {"tool_name": "shell.exec"})
    g.upsert_edge(EdgeType.INVOKED, NodeType.AGENT, "a1", NodeType.TOOL_CALL, "tc1")


def test_exists_path():
    g = InMemoryGraphStore()
    g.upsert_node(NodeType.RESOURCE, "r1", {"labels": ["pii/ssn"], "kind": "db"})
    g.upsert_node(NodeType.TOOL_CALL, "tc1", {"tool_name": "db.query"})
    g.upsert_node(NodeType.TOOL_CALL, "tc2", {"tool_name": "email.send"})
    g.upsert_edge(EdgeType.READ_FROM, NodeType.TOOL_CALL, "tc1", NodeType.RESOURCE, "r1")
    g.upsert_edge(EdgeType.DERIVED_FROM, NodeType.TOOL_CALL, "tc2", NodeType.TOOL_CALL, "tc1")
    assert g.exists_path_to_sink("tc2", ["pii/*"], max_hops=4)
    assert not g.exists_path_to_sink("tc2", ["finance"], max_hops=4)


def test_lru_cache():
    c = LRUCache(capacity=3)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.set("d", 4)
    assert c.get("a") is None
    assert c.get("d") == 4
