"""Normalize raw user input."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class InputInterceptor(BaseInterceptor):
    name = "input"

    def before(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        messages = getattr(event.payload, "messages", None)
        if messages is not None:
            event.metadata["input_length"] = len(str(messages))
        return self._tag(event)
