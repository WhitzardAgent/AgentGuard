"""In-memory human-review queue for held decisions."""
from __future__ import annotations

from typing import Any


class ReviewQueue:
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def enqueue(self, event: dict[str, Any], decision: dict[str, Any]) -> None:
        self._items.append({"event": event, "decision": decision})

    def pending(self) -> list[dict[str, Any]]:
        return list(self._items)

    def resolve(self, index: int) -> dict[str, Any] | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None


__all__ = ["ReviewQueue"]
