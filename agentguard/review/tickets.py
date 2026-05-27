"""Approval bridge — stores pending human-check tickets and exposes (approve|deny)."""

from __future__ import annotations

import abc
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApprovalTicket:
    ticket_id: str
    event_dump: dict[str, Any]
    decision_dump: dict[str, Any]
    created_ms: int
    status: str = "pending"   # pending | approved | denied | expired
    resolver: threading.Event = field(default_factory=threading.Event)
    resolved_action: str = ""
    note: str = ""


class ApprovalBridge(abc.ABC):
    @abc.abstractmethod
    def enqueue(self, event_dump: dict[str, Any], decision_dump: dict[str, Any]) -> ApprovalTicket: ...
    @abc.abstractmethod
    def wait(self, ticket_id: str, timeout_s: float) -> ApprovalTicket: ...
    @abc.abstractmethod
    def resolve(self, ticket_id: str, action: str, note: str = "") -> bool: ...
    @abc.abstractmethod
    def pending(self) -> list[ApprovalTicket]: ...


class InMemoryApprovalBridge(ApprovalBridge):
    def __init__(self) -> None:
        self._tickets: dict[str, ApprovalTicket] = {}
        self._lock = threading.Lock()

    def enqueue(self, event_dump: dict[str, Any], decision_dump: dict[str, Any]) -> ApprovalTicket:
        ticket = ApprovalTicket(
            ticket_id=str(uuid.uuid4()),
            event_dump=event_dump,
            decision_dump=decision_dump,
            created_ms=int(time.time() * 1000),
        )
        with self._lock:
            self._tickets[ticket.ticket_id] = ticket
        return ticket

    def wait(self, ticket_id: str, timeout_s: float) -> ApprovalTicket:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        ticket.resolver.wait(timeout=timeout_s)
        if ticket.status == "pending":
            ticket.status = "expired"
        return ticket

    def resolve(self, ticket_id: str, action: str, note: str = "") -> bool:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
        if ticket is None or ticket.status != "pending":
            return False
        ticket.status = "approved" if action == "approve" else "denied"
        ticket.resolved_action = action
        ticket.note = note
        ticket.resolver.set()
        return True

    def pending(self) -> list[ApprovalTicket]:
        with self._lock:
            return [t for t in self._tickets.values() if t.status == "pending"]
