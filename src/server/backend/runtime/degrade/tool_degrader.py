"""Tool-level degradation mapping."""
from __future__ import annotations

TOOL_DEGRADE_MAP = {
    "send_email": "draft_email",
    "delete_file": "move_to_trash",
    "run_shell": "explain_command",
    "external_post": "local_summary",
    "network_write": "draft_request",
}


def degrade_tool(tool_name: str) -> str | None:
    return TOOL_DEGRADE_MAP.get(tool_name)
