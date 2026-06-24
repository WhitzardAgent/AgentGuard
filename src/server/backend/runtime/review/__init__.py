"""In-memory human-review queue for held decisions."""
from __future__ import annotations

import copy
import threading
import time
import uuid
from typing import Any


class ReviewQueue:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._tickets: dict[str, dict[str, Any]] = {}

    def enqueue(
        self,
        *,
        event: dict[str, Any],
        decision: dict[str, Any],
        principal: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        ticket_id = f"ticket-{uuid.uuid4().hex[:12]}"
        ticket = {
            "ticket_id": ticket_id,
            "created_ms": now_ms,
            "status": "pending",
            "event": copy.deepcopy(event),
            "guard_decision": copy.deepcopy(decision),
            "principal": dict(principal or {}),
            "note": "",
            "resolved_ms": None,
            "resolved_decision": None,
        }
        with self._condition:
            self._tickets[ticket_id] = ticket
            self._condition.notify_all()
        return copy.deepcopy(ticket)

    def pending(self) -> list[dict[str, Any]]:
        with self._condition:
            items = [
                copy.deepcopy(ticket)
                for ticket in self._tickets.values()
                if ticket.get("status") == "pending"
            ]
        return sorted(items, key=lambda item: int(item.get("created_ms") or 0))

    def get(self, ticket_id: str) -> dict[str, Any] | None:
        with self._condition:
            ticket = self._tickets.get(ticket_id)
            return copy.deepcopy(ticket) if ticket is not None else None

    def wait(self, ticket_id: str, timeout_s: float | None = None) -> dict[str, Any] | None:
        deadline = None if timeout_s is None else time.monotonic() + max(timeout_s, 0.0)
        with self._condition:
            while True:
                ticket = self._tickets.get(ticket_id)
                if ticket is None:
                    return None
                if ticket.get("status") != "pending":
                    return copy.deepcopy(ticket)
                if timeout_s is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return copy.deepcopy(ticket)
                    self._condition.wait(remaining)
                else:
                    self._condition.wait()

    def resolve(self, ticket_id: str, approved: bool, note: str = "") -> dict[str, Any] | None:
        clean_note = str(note or "").strip()
        with self._condition:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket.get("status") != "pending":
                return None
            resolved_ms = int(time.time() * 1000)
            status = "approved" if approved else "denied"
            ticket["status"] = status
            ticket["note"] = clean_note
            ticket["resolved_ms"] = resolved_ms
            ticket["resolved_decision"] = _build_resolved_decision(
                ticket_id=ticket_id,
                original=ticket.get("guard_decision") or {},
                approved=approved,
                note=clean_note,
                resolved_ms=resolved_ms,
            )
            self._condition.notify_all()
            return copy.deepcopy(ticket)


def _build_resolved_decision(
    *,
    ticket_id: str,
    original: dict[str, Any],
    approved: bool,
    note: str,
    resolved_ms: int,
) -> dict[str, Any]:
    metadata = dict(original.get("metadata") or {})
    metadata.update(
        {
            "review_ticket_id": ticket_id,
            "review_status": "approved" if approved else "denied",
            "review_note": note,
            "review_resolved_ms": resolved_ms,
            "review_required_decision_type": original.get("decision_type"),
        }
    )
    reason = note or (
        "Approved after human review."
        if approved
        else "Denied after human review."
    )
    return {
        **copy.deepcopy(original),
        "decision_type": "allow" if approved else "deny",
        "reason": reason,
        "metadata": metadata,
    }


__all__ = ["ReviewQueue"]
