"""Base interceptor. Interceptors normalize and annotate; they never decide."""
from __future__ import annotations

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class BaseInterceptor:
    name: str = "base"

    def before(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def after(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def _tag(self, event: RuntimeEvent) -> RuntimeEvent:
        event.metadata.setdefault("interceptors", [])
        if self.name not in event.metadata["interceptors"]:
            event.metadata["interceptors"].append(self.name)
        return event
