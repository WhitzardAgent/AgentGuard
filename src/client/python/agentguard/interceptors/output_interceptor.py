"""Normalize final output events."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class OutputInterceptor(BaseInterceptor):
    name = "output"

    def after(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return self._tag(event)
