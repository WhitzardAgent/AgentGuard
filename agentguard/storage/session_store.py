"""Hot-path state cache abstraction. Default in-memory, Redis backend optional.

Provides key-value, set, and capped-list operations used by the context
collector and graph writer on the synchronous fast path.
"""

from __future__ import annotations

import abc
import json
import threading
import time
from collections import defaultdict, deque
from typing import Any, Iterable

RECENT_TOOLS_CAP = 32
TRACE_LOG_CAP = 256
TRACE_RICH_CAP = 256   # same depth for rich trace entries


class CACHE_KEYS:
    """Cache key templates."""

    RECENT_TOOLS = "ag:sess:{session_id}:recent_tools"
    LABELS = "ag:sess:{session_id}:labels"
    FEATURE = "ag:feat:{session_id}:{feature_key}"
    PROVENANCE = "ag:prov:{resource_id}"
    TRACE_LOG = "ag:sess:{session_id}:trace"
    TRACE_RICH = "ag:sess:{session_id}:trace_rich"  # rich records: args + result

    @staticmethod
    def recent_tools(session_id: str) -> str:
        return CACHE_KEYS.RECENT_TOOLS.format(session_id=session_id)

    @staticmethod
    def labels(session_id: str) -> str:
        return CACHE_KEYS.LABELS.format(session_id=session_id)

    @staticmethod
    def feature(session_id: str, feature_key: str) -> str:
        return CACHE_KEYS.FEATURE.format(session_id=session_id, feature_key=feature_key)

    @staticmethod
    def provenance(resource_id: str) -> str:
        return CACHE_KEYS.PROVENANCE.format(resource_id=resource_id)

    @staticmethod
    def trace_log(session_id: str) -> str:
        return CACHE_KEYS.TRACE_LOG.format(session_id=session_id)

    @staticmethod
    def trace_rich(session_id: str) -> str:
        return CACHE_KEYS.TRACE_RICH.format(session_id=session_id)


FEATURE_TTL_MS = 30_000


class StateCache(abc.ABC):
    """Abstract key-value + set + capped-list API used by the hot path."""

    @abc.abstractmethod
    def get(self, key: str) -> str | None: ...
    @abc.abstractmethod
    def set(self, key: str, value: str, ttl_ms: int | None = None) -> None: ...
    @abc.abstractmethod
    def sadd(self, key: str, *members: str) -> None: ...
    @abc.abstractmethod
    def smembers(self, key: str) -> set[str]: ...
    @abc.abstractmethod
    def lpush_capped(self, key: str, value: str, cap: int = RECENT_TOOLS_CAP) -> None: ...
    @abc.abstractmethod
    def lrange(self, key: str, start: int, end: int) -> list[str]: ...

    # ── trace log: chronological tool-call sequence ──────────────────────
    @abc.abstractmethod
    def append_trace(
        self,
        key: str,
        tool_name: str,
        ts_ms: int,
        cap: int = TRACE_LOG_CAP,
    ) -> None: ...
    @abc.abstractmethod
    def read_trace(self, key: str) -> list[tuple[str, int]]: ...

    # ── rich trace log: args + result per call ───────────────────────────
    @abc.abstractmethod
    def append_trace_rich(
        self,
        key: str,
        entry: dict[str, Any],
        cap: int = TRACE_RICH_CAP,
    ) -> None:
        """Append a rich trace entry ``{"tool": str, "args": dict, "result": Any, "ts_ms": int}``."""
        ...

    @abc.abstractmethod
    def update_trace_result_last(self, key: str, tool_name: str, result: Any) -> None:
        """Back-fill the result field on the most-recent entry for ``tool_name``."""
        ...

    @abc.abstractmethod
    def read_trace_rich(self, key: str) -> list[dict[str, Any]]:
        """Return all rich trace entries, oldest-first."""
        ...

    def clear(self) -> None:
        """Drop every cached entry. Optional in subclasses."""
        return None


class InMemoryStateCache(StateCache):
    """Thread-safe, process-local cache. Good for tests and small deployments."""

    def __init__(self) -> None:
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._sets: dict[str, set[str]] = defaultdict(set)
        self._lists: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=RECENT_TOOLS_CAP))
        # Chronological trace log: oldest-first, (tool_name, ts_ms) tuples.
        self._traces: dict[str, deque[tuple[str, int]]] = defaultdict(
            lambda: deque(maxlen=TRACE_LOG_CAP)
        )
        # Rich trace log: oldest-first, dict entries with args + result.
        self._traces_rich: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=TRACE_RICH_CAP)
        )
        self._lock = threading.RLock()

    def _expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and time.time() > expires_at

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._kv.get(key)
            if item is None:
                return None
            value, expires_at = item
            if self._expired(expires_at):
                self._kv.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl_ms: int | None = None) -> None:
        with self._lock:
            expires_at = time.time() + ttl_ms / 1000.0 if ttl_ms else None
            self._kv[key] = (value, expires_at)

    def sadd(self, key: str, *members: str) -> None:
        with self._lock:
            self._sets[key].update(members)

    def smembers(self, key: str) -> set[str]:
        with self._lock:
            return set(self._sets.get(key, set()))

    def lpush_capped(self, key: str, value: str, cap: int = RECENT_TOOLS_CAP) -> None:
        with self._lock:
            dq = self._lists[key]
            if dq.maxlen != cap:
                dq = deque(dq, maxlen=cap)
                self._lists[key] = dq
            dq.appendleft(value)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        with self._lock:
            dq = self._lists.get(key)
            if not dq:
                return []
            items = list(dq)
            if end < 0:
                end = len(items) + end + 1
            else:
                end = end + 1
            return items[start:end]

    def append_trace(
        self,
        key: str,
        tool_name: str,
        ts_ms: int,
        cap: int = TRACE_LOG_CAP,
    ) -> None:
        with self._lock:
            dq = self._traces[key]
            if dq.maxlen != cap:
                dq = deque(dq, maxlen=cap)
                self._traces[key] = dq
            dq.append((tool_name, ts_ms))

    def read_trace(self, key: str) -> list[tuple[str, int]]:
        with self._lock:
            dq = self._traces.get(key)
            return list(dq) if dq else []

    def append_trace_rich(
        self,
        key: str,
        entry: dict[str, Any],
        cap: int = TRACE_RICH_CAP,
    ) -> None:
        with self._lock:
            dq = self._traces_rich[key]
            if dq.maxlen != cap:
                dq = deque(dq, maxlen=cap)
                self._traces_rich[key] = dq
            dq.append(dict(entry))   # shallow copy to avoid aliasing

    def update_trace_result_last(self, key: str, tool_name: str, result: Any) -> None:
        """Back-fill result on the most-recent entry whose tool matches ``tool_name``."""
        with self._lock:
            dq = self._traces_rich.get(key)
            if not dq:
                return
            for entry in reversed(dq):
                if entry.get("tool") == tool_name:
                    try:
                        # serialise to make sure result is JSON-safe for remote/Redis compat
                        json.dumps(result)
                        entry["result"] = result
                    except (TypeError, ValueError):
                        entry["result"] = str(result)
                    return

    def read_trace_rich(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            dq = self._traces_rich.get(key)
            return [dict(e) for e in dq] if dq else []

    def clear(self) -> None:
        with self._lock:
            self._kv.clear()
            self._sets.clear()
            self._lists.clear()
            self._traces.clear()
            self._traces_rich.clear()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_state_cache(url: str | None) -> StateCache:
    """Construct a StateCache from a connection URL.

    * ``None`` / ``""`` / ``"memory"`` → :class:`InMemoryStateCache`
    * ``redis://[:password@]host[:port][/db]`` → :class:`RedisStateCache`

    The Redis backend requires the optional ``redis`` extra to be installed
    (``pip install agentguard[redis]``).
    """
    if not url or url in {"memory", "in-memory", "inmemory"}:
        return InMemoryStateCache()
    if url.startswith(("redis://", "rediss://", "unix://")):
        from agentguard.storage.redis_state_cache import RedisStateCache
        return RedisStateCache.from_url(url)
    raise ValueError(f"unsupported state cache backend: {url!r}")
