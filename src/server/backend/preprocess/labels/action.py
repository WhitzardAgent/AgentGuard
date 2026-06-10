"""Action labels describing the side effect class of an event."""
from __future__ import annotations

ACTION_LABELS = (
    "read",
    "execute",
    "respond",
)

_EVENT_ACTION = {
    "llm_input": "read",
    "llm_output": "respond",
    "tool_invoke": "execute",
    "tool_result": "read",
}


def action_from_event_type(event_type: str) -> str:
    return _EVENT_ACTION.get(event_type, "read")
