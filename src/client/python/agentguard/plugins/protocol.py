"""Client plugin protocol: hook names and value-transforming hooks."""
from __future__ import annotations

# Hooks that transform and return a value.
TRANSFORM_HOOKS = (
    "on_event",
    "on_llm_input",
    "on_llm_output",
    "on_llm_thought",
    "on_tool_invoke",
    "on_tool_result",
    "on_before_remote_decision",
    "on_after_remote_decision",
)
# Hooks that only notify.
NOTIFY_HOOKS = ("on_session_start", "on_session_end")

ALL_HOOKS = TRANSFORM_HOOKS + NOTIFY_HOOKS
