"""Normalize tool invocation events and attach capability metadata."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class ToolInterceptor(BaseInterceptor):
    name = "tool"

    def before(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        event.metadata.setdefault("tool_name", getattr(event.payload, "tool_name", None))
        return self._tag(event)
