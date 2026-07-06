"""Qwen3Guard plugin for LLM output events."""
from __future__ import annotations

from shared.schemas.events import EventType, RuntimeEvent

from backend.runtime.plugins.common.patterns import text_of
from backend.runtime.plugins.qwen3guard import Qwen3GuardPluginBase
from backend.runtime.plugins.registry import register


@register(
    name="qwen3guard_output",
    description="Classify LLM output with Qwen3Guard after model execution.",
)
class Qwen3GuardOutputPlugin(Qwen3GuardPluginBase):
    event_types = [EventType.LLM_OUTPUT]
    policy_scope = "llm_output"

    def _messages_for_event(self, event: RuntimeEvent) -> list[dict[str, str]]:
        content = text_of(event.payload.output)
        return [{"role": "assistant", "content": content}] if content else []
