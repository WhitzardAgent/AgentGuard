"""Audit recorder: turns events+decisions into redacted audit records."""
from __future__ import annotations

from typing import Any

from agentguard.audit.logger import AuditLogger
from agentguard.audit.redactor import redact
from agentguard.audit.trace import Trace
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import RuntimeEvent
from agentguard.utils.time import iso_now


class AuditRecorder:
    """Builds redacted audit records and keeps the session trace."""

    def __init__(self, session_id: str, logger: AuditLogger | None = None) -> None:
        self.session_id = session_id
        self.logger = logger or AuditLogger()
        self.trace = Trace(session_id=session_id)

    def record(
        self,
        event: RuntimeEvent,
        decision: GuardDecision | None = None,
        plugin_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.trace.add(event, decision)
        record = {
            "timestamp": iso_now(),
            "session_id": event.context.session_id,
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "decision_type": decision.decision_type.value if decision else None,
            "reason": decision.reason if decision else None,
            "risk_signals": list(event.risk_signals),
            "policy_id": decision.policy_id if decision else None,
            "plugin_results": plugin_results or {},
            "metadata": {
                "payload": event.payload,
                "decision_metadata": decision.metadata if decision else {},
            },
        }
        safe = redact(record)
        self.logger.write(safe)
        return safe

    def records(self) -> list[dict[str, Any]]:
        return self.logger.records()

    def flush(self) -> list[dict[str, Any]]:
        return self.logger.flush()
