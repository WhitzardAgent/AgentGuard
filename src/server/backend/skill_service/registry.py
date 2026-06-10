"""Server-side view of the project skill registry."""
from __future__ import annotations

from typing import Any


class SkillRegistry:
    def __init__(self) -> None:
        self._registry = None

    def _load(self):
        if self._registry is None:
            try:
                from skills.registry import get_registry  # noqa: PLC0415
            except ImportError:
                self._registry = _EmptySkillRegistry()
                return self._registry

            self._registry = get_registry()
        return self._registry

    def names(self) -> list[str]:
        return self._load().names()

    def get(self, name: str) -> Any:
        return self._load().get(name)


class _EmptySkillRegistry:
    def names(self) -> list[str]:
        return []

    def get(self, name: str) -> Any:
        _ = name
        return None
