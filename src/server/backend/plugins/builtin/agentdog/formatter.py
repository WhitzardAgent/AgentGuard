"""Extract the trajectory window for AgentDoG from a remote request."""
from __future__ import annotations

from typing import Any


def _flatten(e: dict[str, Any]) -> dict[str, Any]:
    payload = e.get("payload") or {}
    return {
        "event_id": e.get("event_id"),
        "event_type": e.get("event_type"),
        "tool_name": payload.get("tool_name"),
        "capabilities": payload.get("capabilities") or [],
        "risk_signals": e.get("risk_signals") or [],
        "summary": str(
            payload.get("text") or payload.get("result") or payload.get("arguments") or ""
        )[:200],
    }


def extract_trajectory(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer the proxy-formatted window; fall back to the raw window.

    The current event is always appended (deduplicated) so the diagnosis can
    reason about the action being evaluated, not only its precursors.
    """
    ext = (request.get("plugin_extensions") or {}).get("agentdog") or {}
    window = ext.get("trajectory_window")
    if window:
        out = list(window)
    else:
        out = [_flatten(e) for e in request.get("trajectory_window") or []]

    cur = request.get("current_event") or {}
    if cur:
        flat = _flatten(cur)
        seen = {e.get("event_id") for e in out if e.get("event_id")}
        if not flat.get("event_id") or flat["event_id"] not in seen:
            out.append(flat)
    return out
