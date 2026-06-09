"""Action labels describing the side effect class of an event."""
from __future__ import annotations

ACTION_LABELS = (
    "read",
    "write",
    "send",
    "execute",
    "query",
    "respond",
    "think",
)

_EVENT_ACTION = {
    "file_read": "read",
    "memory_read": "read",
    "file_write": "write",
    "memory_write": "write",
    "network_request": "send",
    "tool_invoke": "execute",
    "tool_result": "read",
    "final_response": "respond",
    "llm_thought": "think",
}


def action_from_event_type(event_type: str) -> str:
    return _EVENT_ACTION.get(event_type, "read")
