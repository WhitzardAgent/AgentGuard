"""Client-side cache for locally decided events awaiting server sync."""
from __future__ import annotations

import threading
from typing import Any

from agentguard.plugins.base import CheckResult
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent


class ClientSyncBuffer:
    """Thread-safe buffer for local plugin decisions not yet seen by the server."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add_local_decision(
        self,
        *,
        event: RuntimeEvent,
        context: RuntimeContext,
        check: CheckResult,
        decision: GuardDecision,
        route: str,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "source": "client_local_plugin",
            "route": route,
            "event": event.to_dict(),
            "context": context.to_dict(),
            "decision": decision.to_dict(),
            "plugin_result": _plugin_result_dict(check),
            "plugin_input": {
                "event": event.to_dict(),
                "context": context.to_dict(),
            },
            "extensions": extensions or {},
        }
        with self._lock:
            self._entries.append(entry)

    def has_entries(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def pop_all(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._entries
            self._entries = []
            return entries

    def restore_front(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        with self._lock:
            self._entries = list(entries) + self._entries

    def remove_entries(self, entries: list[dict[str, Any]]) -> None:
        event_ids = {
            (entry.get("event") or {}).get("event_id")
            for entry in entries
            if isinstance(entry.get("event"), dict)
        }
        event_ids.discard(None)
        if not event_ids:
            return
        with self._lock:
            self._entries = [
                entry
                for entry in self._entries
                if not (
                    isinstance(entry.get("event"), dict)
                    and entry["event"].get("event_id") in event_ids
                )
            ]

    def clear(self) -> None:
        with self._lock:
            self._entries = []

    def build_trace_upload(
        self,
        *,
        context: RuntimeContext,
        entries: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        return {
            "session_id": context.session_id,
            "agent_id": context.agent_id,
            "user_id": context.user_id,
            "reason": reason,
            "entries": entries,
        }


def _plugin_result_dict(check: CheckResult) -> dict[str, Any]:
    return {
        "risk_signals": list(check.risk_signals),
        "is_final": check.is_final,
        "decision_candidate": (
            check.decision_candidate.to_dict() if check.decision_candidate else None
        ),
        "metadata": dict(check.metadata),
    }
