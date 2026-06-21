"""Normalize memory read/write events."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class MemoryInterceptor(BaseInterceptor):
    name = "memory"

    def before(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return self._tag(event)
