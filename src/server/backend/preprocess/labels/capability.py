"""Capability labels (server side mirrors client capability vocabulary)."""
from __future__ import annotations

CAPABILITIES = {
    "read_file",
    "write_file",
    "network",
    "external_send",
    "shell",
    "database_write",
    "payment",
    "browser_action",
}

# Map tool-name keywords to inferred capabilities.
TOOL_NAME_CAPABILITY_HINTS = {
    "email": ["external_send"],
    "send": ["external_send"],
    "post": ["external_send", "network"],
    "http": ["network"],
    "fetch": ["network"],
    "shell": ["shell"],
    "exec": ["shell"],
    "bash": ["shell"],
    "write": ["write_file"],
    "save": ["write_file"],
    "delete": ["write_file"],
    "read": ["read_file"],
    "db": ["database_write"],
    "sql": ["database_write"],
    "pay": ["payment"],
    "browser": ["browser_action"],
}


def infer_capabilities(tool_name: str) -> list[str]:
    name = (tool_name or "").lower()
    caps: list[str] = []
    for kw, kcaps in TOOL_NAME_CAPABILITY_HINTS.items():
        if kw in name:
            for c in kcaps:
                if c not in caps:
                    caps.append(c)
    return caps
