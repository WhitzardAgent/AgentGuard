"""Replay audit records back into a trace-like structure."""
from __future__ import annotations

from typing import Any


def replay_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Group audit records by session for replay/inspection."""
    sessions: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        sid = r.get("session_id") or "unknown"
        sessions.setdefault(sid, []).append(r)
    return {
        "session_count": len(sessions),
        "sessions": {sid: {"events": evs, "count": len(evs)} for sid, evs in sessions.items()},
    }
