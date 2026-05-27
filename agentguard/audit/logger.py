"""Append-only audit writer. Default sink is an in-process ring buffer.

Pluggable: pass a `sink=callable(record: dict)` to redirect to Kafka / S3 / OLAP.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Any, Callable

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent


SinkFn = Callable[[dict[str, Any]], None]


class AuditLogWriter:
    """Append-only, thread-safe ring buffer for audit records.

    When occupancy reaches 80% of `buffer_size` a warning is emitted once.
    After the buffer is full, the oldest entry is evicted and `dropped_count`
    is incremented so callers can detect data loss.
    """

    def __init__(self, sink: SinkFn | None = None, buffer_size: int = 10_000) -> None:
        self._sink = sink
        self._buffer_size = buffer_size
        self._buf: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self.dropped_count: int = 0
        self._warned_full: bool = False

    def log(self, event: RuntimeEvent, decision: Decision | None = None) -> None:
        record = {
            "event": event.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json") if decision else None,
        }
        with self._lock:
            current = len(self._buf)
            if current >= self._buffer_size:
                # deque will evict the oldest; track it
                self.dropped_count += 1
            elif not self._warned_full and current >= int(self._buffer_size * 0.80):
                import logging as _log
                _log.getLogger(__name__).warning(
                    "AuditLogWriter buffer at %.0f%% capacity (%d/%d). "
                    "Consider increasing buffer_size or attaching a persistent sink.",
                    100 * current / self._buffer_size,
                    current,
                    self._buffer_size,
                )
                self._warned_full = True
            self._buf.append(record)
        if self._sink is not None:
            try:
                self._sink(record)
            except Exception:
                pass

    def recent(self, n: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buf)[-n:]

    def dumps(self) -> str:
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in self.recent(10_000))
