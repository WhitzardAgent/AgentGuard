"""Normalize LLM input/output events."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class LLMInterceptor(BaseInterceptor):
    name = "llm"

    def before(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return self._tag(event)

    def after(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        out = event.payload.get("output")
        if out is not None:
            event.metadata["output_type"] = type(out).__name__
        return self._tag(event)
