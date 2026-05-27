"""Provenance helper used by SDK / adapters to declare 'where data came from'."""

from __future__ import annotations

from agentguard.storage.session_store import StateCache, CACHE_KEYS


class ProvenanceTracker:
    """Thin wrapper on top of the StateCache for session-scoped label propagation."""

    def __init__(self, cache: StateCache) -> None:
        self._cache = cache

    def tag(self, session_id: str, resource_id: str, *labels: str) -> None:
        if not labels:
            return
        self._cache.sadd(CACHE_KEYS.provenance(resource_id), *labels)
        self._cache.sadd(CACHE_KEYS.labels(session_id), *labels)

    def labels_for(self, resource_id: str) -> set[str]:
        return self._cache.smembers(CACHE_KEYS.provenance(resource_id))

    def session_labels(self, session_id: str) -> set[str]:
        return self._cache.smembers(CACHE_KEYS.labels(session_id))
