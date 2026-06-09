"""JSONL audit logger for the client."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from agentguard.utils.json import safe_dumps


class AuditLogger:
    """Append-only JSONL audit sink. In-memory buffer plus optional file."""

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path) if path else None
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        line = safe_dumps(record)
        with self._lock:
            self._buffer.append(record)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer)

    def flush(self) -> list[dict[str, Any]]:
        """Return buffered records (file is already flushed on write)."""
        return self.records()

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
