"""Normalize LLM internal thought/reasoning events."""
from __future__ import annotations

from agentguard.interceptors.base import BaseInterceptor
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class ThoughtInterceptor(BaseInterceptor):
    name = "thought"

    def before(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        thought = getattr(event.payload, "thought", None)
        if thought is None:
            thought = getattr(event.payload, "output", None)
        if thought is not None:
            event.metadata["thought_length"] = len(str(thought))
        return self._tag(event)
