"""Format the trajectory window for the AgentDoG server plugin."""
from __future__ import annotations

from typing import Any

from agentguard.plugins.builtin.agentdog_proxy.config import AgentDoGProxyConfig
from agentguard.plugins.builtin.agentdog_proxy.redactor import redact_event


def format_trajectory(
    window: list[dict[str, Any]], config: AgentDoGProxyConfig
) -> list[dict[str, Any]]:
    """Produce a compact, redacted trajectory for diagnosis."""
    out: list[dict[str, Any]] = []
    for raw in window[-config.window_size :]:
        etype = raw.get("event_type")
        if etype == "tool_result" and not config.include_tool_results:
            continue
        if etype == "llm_output" and not config.include_llm_outputs:
            continue
        safe = redact_event(raw, config.redaction_level)
        payload = safe.get("payload") or {}
        out.append(
            {
                "event_id": safe.get("event_id"),
                "event_type": etype,
                "tool_name": payload.get("tool_name"),
                "capabilities": payload.get("capabilities") or [],
                "risk_signals": safe.get("risk_signals") or [],
                "summary": _summarize(payload),
            }
        )
    return out


def _summarize(payload: dict[str, Any]) -> str:
    for key in ("text", "result", "arguments", "output", "messages"):
        if key in payload and payload[key] is not None:
            return str(payload[key])[:200]
    return ""
