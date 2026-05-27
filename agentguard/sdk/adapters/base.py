"""Common adapter base."""

from __future__ import annotations

import abc
from typing import Any, TYPE_CHECKING

from agentguard.models.decisions import Decision
from agentguard.models.events import EventType, RuntimeEvent
from agentguard.runtime.dispatcher import Pipeline

if TYPE_CHECKING:
    from agentguard.sdk.guard import Guard


class BaseAdapter(abc.ABC):
    def __init__(self, pipeline: Pipeline, guard: "Guard") -> None:
        self.pipeline = pipeline
        self.guard = guard

    @abc.abstractmethod
    def install(self, framework_obj: Any) -> None: ...

    def _dispatch_attempt(self, event: RuntimeEvent) -> Decision:
        return self.pipeline.handle_attempt(event)

    def _dispatch_result(self, event: RuntimeEvent) -> None:
        self.pipeline.handle_result(
            event.model_copy(update={"event_type": EventType.TOOL_CALL_RESULT}))
