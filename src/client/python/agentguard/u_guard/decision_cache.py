"""Bounded decision cache keyed by stable event hash."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent


class DecisionCache:
    def __init__(self, capacity: int = 512, ttl_s: float | None = None) -> None:
        self.capacity = capacity
        self.ttl_s = ttl_s
        self._store: OrderedDict[str, tuple[float, GuardDecision]] = OrderedDict()
        self._lock = threading.Lock()

    def key(self, event: RuntimeEvent) -> str:
        return event.stable_hash()

    def get(self, event: RuntimeEvent) -> GuardDecision | None:
        k = self.key(event)
        with self._lock:
            item = self._store.get(k)
            if not item:
                return None
            ts, decision = item
            if self.ttl_s is not None and (time.time() - ts) > self.ttl_s:
                self._store.pop(k, None)
                return None
            self._store.move_to_end(k)
            return decision

    def put(self, event: RuntimeEvent, decision: GuardDecision) -> None:
        # Do not cache interactive/pending decisions.
        if decision.requires_user:
            return
        k = self.key(event)
        with self._lock:
            self._store[k] = (time.time(), decision)
            self._store.move_to_end(k)
            while len(self._store) > self.capacity:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
