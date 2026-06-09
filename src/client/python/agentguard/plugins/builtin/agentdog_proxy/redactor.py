"""Redaction for AgentDoG proxy payloads."""
from __future__ import annotations

from typing import Any

from agentguard.audit.redactor import redact


def redact_event(event: dict[str, Any], level: str = "standard") -> dict[str, Any]:
    """Redact a serialized event before sending to the server plugin."""
    safe = redact(event)
    if level == "strict":
        # Strict mode drops raw payload, keeping only structural signals.
        safe["payload"] = {"tool_name": (event.get("payload") or {}).get("tool_name")}
    return safe
