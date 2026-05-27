from __future__ import annotations

from agentguard.models.tool_catalog import ToolCatalogEntry, ToolCatalogLabels
from agentguard.storage.tool_catalog_store import InMemoryToolCatalogStore


def test_tool_catalog_entry_public_dict_includes_owner_agent_id():
    entry = ToolCatalogEntry(
        owner_agent_id="agent-a",
        name="email.send",
        input_params=["to"],
    )

    assert entry.to_public_dict()["owner_agent_id"] == "agent-a"


def test_store_keeps_same_tool_name_for_different_agents():
    store = InMemoryToolCatalogStore()
    first = ToolCatalogEntry(owner_agent_id="agent-a", name="shell.exec")
    second = ToolCatalogEntry(owner_agent_id="agent-b", name="shell.exec")

    store.upsert_tool(first)
    store.upsert_tool(second)

    assert store.get_tool("shell.exec", "agent-a") is not None
    assert store.get_tool("shell.exec", "agent-b") is not None
    assert [entry.owner_agent_id for entry in store.list_tools()] == ["agent-a", "agent-b"]


def test_store_overwrites_only_within_same_agent_scope():
    store = InMemoryToolCatalogStore()
    first = ToolCatalogEntry(
        owner_agent_id="agent-a",
        name="email.send",
        input_params=["to"],
    )
    second = ToolCatalogEntry(
        owner_agent_id="agent-a",
        name="email.send",
        input_params=["to", "subject"],
    )

    store.upsert_tool(first)
    store.upsert_tool(second)

    stored = store.get_tool("email.send", "agent-a")
    assert stored is not None
    assert stored.input_params == ["to", "subject"]
    assert len(store.list_tools(agent_id="agent-a")) == 1


def test_store_list_tools_can_filter_by_agent():
    store = InMemoryToolCatalogStore()
    store.upsert_tool(ToolCatalogEntry(owner_agent_id="agent-a", name="email.send"))
    store.upsert_tool(ToolCatalogEntry(owner_agent_id="agent-b", name="db.query"))

    entries = store.list_tools(agent_id="agent-b")

    assert len(entries) == 1
    assert entries[0].owner_agent_id == "agent-b"
    assert entries[0].name == "db.query"


def test_update_tool_labels_updates_labels_and_keeps_input_params():
    store = InMemoryToolCatalogStore()
    store.upsert_tool(
        ToolCatalogEntry(
            owner_agent_id="agent-a",
            name="email.send",
            labels=ToolCatalogLabels(boundary="external", sensitivity="moderate", integrity="trusted", tags=["old"]),
            input_params=["to", "subject"],
        )
    )

    updated = store.update_tool_labels(
        "agent-a",
        "email.send",
        ToolCatalogLabels(boundary="internal", sensitivity="low", integrity="trusted", tags=["new"]),
    )

    assert updated is not None
    assert updated.labels.boundary == "internal"
    assert updated.labels.sensitivity == "low"
    assert updated.labels.tags == ["new"]
    assert updated.input_params == ["to", "subject"]


def test_update_tool_labels_returns_none_for_missing_tool():
    store = InMemoryToolCatalogStore()

    updated = store.update_tool_labels(
        "agent-a",
        "email.send",
        ToolCatalogLabels(boundary="internal", sensitivity="low", integrity="trusted"),
    )

    assert updated is None


def test_after_write_hook_runs_for_upsert_and_label_updates():
    store = InMemoryToolCatalogStore()
    captured: list[tuple[str, str, str]] = []
    store.set_after_write_hook(
        lambda entry: captured.append(
            (entry.owner_agent_id, entry.name, entry.labels.sensitivity)
        )
    )

    store.upsert_tool(
        ToolCatalogEntry(
            owner_agent_id="agent-a",
            name="email.send",
            labels=ToolCatalogLabels(boundary="external", sensitivity="moderate", integrity="trusted"),
        )
    )
    store.update_tool_labels(
        "agent-a",
        "email.send",
        ToolCatalogLabels(boundary="external", sensitivity="high", integrity="trusted"),
    )

    assert captured == [
        ("agent-a", "email.send", "moderate"),
        ("agent-a", "email.send", "high"),
    ]
