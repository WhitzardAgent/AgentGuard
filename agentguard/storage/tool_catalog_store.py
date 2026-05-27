"""Tool catalog storage for remote runtime control-plane APIs."""

from __future__ import annotations

import abc
import logging
import threading
from collections.abc import Callable

from agentguard.models.tool_catalog import ToolCatalogEntry, ToolCatalogLabels

log = logging.getLogger(__name__)


class ToolCatalogReadAPI(abc.ABC):
    @abc.abstractmethod
    def list_tools(self, agent_id: str | None = None) -> list[ToolCatalogEntry]: ...

    @abc.abstractmethod
    def get_tool(self, name: str, agent_id: str) -> ToolCatalogEntry | None: ...


class ToolCatalogWriteAPI(abc.ABC):
    @abc.abstractmethod
    def upsert_tool(self, entry: ToolCatalogEntry) -> ToolCatalogEntry: ...

    @abc.abstractmethod
    def update_tool_labels(
        self,
        agent_id: str,
        name: str,
        labels: ToolCatalogLabels,
    ) -> ToolCatalogEntry | None: ...

    @abc.abstractmethod
    def clear(self) -> None: ...


class InMemoryToolCatalogStore(ToolCatalogReadAPI, ToolCatalogWriteAPI):
    """Thread-safe in-memory tool catalog keyed by (agent, tool name)."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], ToolCatalogEntry] = {}
        self._lock = threading.RLock()
        self._after_write_hook: Callable[[ToolCatalogEntry], None] | None = None

    def set_after_write_hook(
        self,
        hook: Callable[[ToolCatalogEntry], None] | None,
    ) -> None:
        with self._lock:
            self._after_write_hook = hook

    def list_tools(self, agent_id: str | None = None) -> list[ToolCatalogEntry]:
        with self._lock:
            items = self._tools.items()
            if agent_id is not None:
                items = (
                    (key, entry)
                    for key, entry in items
                    if key[0] == agent_id
                )
            return [
                entry for _, entry in sorted(items, key=lambda item: (item[0][0], item[0][1]))
            ]

    def get_tool(self, name: str, agent_id: str) -> ToolCatalogEntry | None:
        with self._lock:
            return self._tools.get((agent_id, name))

    def upsert_tool(self, entry: ToolCatalogEntry) -> ToolCatalogEntry:
        with self._lock:
            stored = entry.with_updated_timestamp()
            self._tools[(stored.owner_agent_id, stored.name)] = stored
        self._run_after_write_hook(stored)
        return stored

    def update_tool_labels(
        self,
        agent_id: str,
        name: str,
        labels: ToolCatalogLabels,
    ) -> ToolCatalogEntry | None:
        with self._lock:
            current = self._tools.get((agent_id, name))
            if current is None:
                return None
            updated = current.model_copy(update={"labels": labels}).with_updated_timestamp()
            self._tools[(agent_id, name)] = updated
        self._run_after_write_hook(updated)
        return updated

    def clear(self) -> None:
        with self._lock:
            self._tools.clear()

    def _run_after_write_hook(self, entry: ToolCatalogEntry) -> None:
        with self._lock:
            hook = self._after_write_hook
        if hook is None:
            return
        try:
            hook(entry)
        except Exception as exc:  # pragma: no cover - defensive log path
            log.warning("tool catalog after-write hook failed for %s/%s: %s", entry.owner_agent_id, entry.name, exc)
