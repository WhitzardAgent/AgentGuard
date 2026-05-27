"""Programmatic API for approving / denying pending human-check tickets."""

from __future__ import annotations

from typing import Any

from agentguard.review.tickets import ApprovalBridge


class ApprovalConsole:
    def __init__(self, bridge: ApprovalBridge) -> None:
        self._bridge = bridge

    def list_pending(self) -> list[dict[str, Any]]:
        out = []
        for t in self._bridge.pending():
            out.append({
                "ticket_id": t.ticket_id,
                "created_ms": t.created_ms,
                "event": t.event_dump,
                "decision": t.decision_dump,
            })
        return out

    def approve(self, ticket_id: str, note: str = "") -> bool:
        return self._bridge.resolve(ticket_id, "approve", note)

    def deny(self, ticket_id: str, note: str = "") -> bool:
        return self._bridge.resolve(ticket_id, "deny", note)
