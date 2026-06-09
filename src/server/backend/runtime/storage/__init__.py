"""In-memory trace/decision storage."""
from __future__ import annotations

from typing import Any


class TraceStore:
    def __init__(self) -> None:
        self._traces: dict[str, list[dict[str, Any]]] = {}

    def append(self, session_id: str, record: dict[str, Any]) -> None:
        self._traces.setdefault(session_id, []).append(record)

    def get(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._traces.get(session_id, []))

    def sessions(self) -> list[str]:
        return list(self._traces.keys())


__all__ = ["TraceStore"]
