"""Server audit logger with in-memory and optional JSONL storage."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from shared.audit.redactor import redact
from shared.utils.json import safe_dumps
from shared.utils.time import iso_now


class AuditLogger:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path) if path else None
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event: dict[str, Any],
        decision: dict[str, Any],
        plugin_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rec = redact(
            {
                "timestamp": iso_now(),
                "session_id": (event.get("context") or {}).get("session_id"),
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "decision_type": decision.get("decision_type"),
                "reason": decision.get("reason"),
                "risk_signals": event.get("risk_signals") or [],
                "policy_id": decision.get("policy_id"),
                "plugin_results": plugin_results or {},
                "metadata": decision.get("metadata") or {},
            }
        )
        with self._lock:
            self._records.append(rec)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(safe_dumps(rec) + "\n")
        return rec

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)
