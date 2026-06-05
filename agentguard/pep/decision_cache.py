"""Small TTL cache for decisions keyed by (policy version, event signature)."""

from __future__ import annotations

import threading
import time

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision
from agentguard.schemas.events import RuntimeEvent
from agentguard.utils.hash import stable_hash


class DecisionCache:
    def __init__(self, *, ttl_seconds: float = 5.0, max_entries: int = 2048) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Decision]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(event: RuntimeEvent, context: RuntimeContext, version: str) -> str:
        return stable_hash(
            {
                "v": version,
                "policy": context.policy,
                "type": event.type.value,
                "tool": event.tool_name,
                "args": event.args,
                "content": event.content,
                "caps": sorted(event.capabilities),
            }
        )

    def get(self, key: str) -> Decision | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, decision = entry
            if now - ts > self.ttl:
                self._store.pop(key, None)
                return None
            return decision.model_copy(update={"source": "cache"})

    def put(self, key: str, decision: Decision) -> None:
        with self._lock:
            if len(self._store) >= self.max_entries:
                # drop oldest ~10% to bound memory
                for old in sorted(self._store, key=lambda k: self._store[k][0])[
                    : max(1, self.max_entries // 10)
                ]:
                    self._store.pop(old, None)
            self._store[key] = (time.monotonic(), decision)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
