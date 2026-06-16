"""Client plugin base. Plugins add signals and hints; they never decide."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent


class ClientPlugin:
    plugin_id: str = "client_plugin"

    def on_session_start(self, context: RuntimeContext) -> None:
        pass

    def on_event(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def on_llm_input(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def on_llm_output(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def on_tool_invoke(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def on_tool_result(self, event: RuntimeEvent, context: RuntimeContext) -> RuntimeEvent:
        return event

    def on_before_remote_decision(
        self, request: dict[str, Any], context: RuntimeContext
    ) -> dict[str, Any]:
        return request

    def on_after_remote_decision(self, response: Any, context: RuntimeContext) -> Any:
        return response

    def on_session_end(self, trace: Any, context: RuntimeContext) -> None:
        pass
