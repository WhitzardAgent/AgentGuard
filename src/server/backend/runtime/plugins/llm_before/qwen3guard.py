"""Qwen3Guard plugin for LLM input events."""
from __future__ import annotations

from shared.schemas.events import EventType, RuntimeEvent

from backend.runtime.plugins.common.patterns import text_of
from backend.runtime.plugins.qwen3guard import Qwen3GuardPluginBase
from backend.runtime.plugins.registry import register


@register(
    name="qwen3guard_input",
    description="Classify LLM input with Qwen3Guard before model execution.",
)
class Qwen3GuardInputPlugin(Qwen3GuardPluginBase):
    event_types = [EventType.LLM_INPUT]
    policy_scope = "llm_input"

    def _messages_for_event(self, event: RuntimeEvent) -> list[dict[str, str]]:
        messages = []
        for item in event.payload.messages:
            role = str(item.get("role") or "user").strip() or "user"
            content = text_of(item.get("content"))
            if content:
                messages.append({"role": role, "content": content})
        return messages
