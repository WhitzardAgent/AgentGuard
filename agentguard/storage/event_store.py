"""Simple LRU cache and append-only event log for policy evaluation."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Hashable


class LRUCache:
    def __init__(self, capacity: int = 1024) -> None:
        self._cap = capacity
        self._data: OrderedDict[Hashable, tuple[Any, float | None]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expires = item
            if expires is not None and time.time() > expires:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: Hashable, value: Any, ttl_ms: int | None = None) -> None:
        with self._lock:
            expires = time.time() + ttl_ms / 1000.0 if ttl_ms else None
            self._data[key] = (value, expires)
            self._data.move_to_end(key)
            while len(self._data) > self._cap:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
