"""Normalize tool result events."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class ToolResultInterceptor(BaseInterceptor):
    name = "tool_result"

    def after(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        if event.payload.get("error"):
            event.metadata["had_error"] = True
        return self._tag(event)
