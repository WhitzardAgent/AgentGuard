"""Base auditor interface, normalized audit result, and trace entry type."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from shared.schemas.decisions import GuardDecision
from shared.schemas.events import RuntimeEvent

AuditLevel = Literal["critical", "high", "warning", "ok"]


@dataclass
class AuditResult:
    level: AuditLevel = "ok"
    reason: str = "No issue detected in trace."
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def ok(reason: str = "No issue detected in trace.") -> "AuditResult":
        return AuditResult(level="ok", reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass
class AuditTraceEntry:
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    reason: str | None = None
    event: RuntimeEvent | None = None
    decision: GuardDecision | None = None
    checker_result: dict[str, Any] = field(default_factory=dict)
    plugin_results: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditTraceEntry":
        event = _runtime_event_from_trace_entry_data(data)
        decision = _decision_from_trace_entry_data(data)
        event_context = event.context if event is not None else None
        session_id = str(
            data.get("session_id")
            or (event_context.session_id if event_context and event_context.session_id else "unknown")
        )
        agent_id = _string_or_none(
            data.get("agent_id")
            or (event_context.agent_id if event_context else None)
        )
        user_id = _string_or_none(
            data.get("user_id")
            or (event_context.user_id if event_context else None)
        )
        reason = _string_or_none(data.get("reason"))
        return cls(
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            reason=reason,
            event=event,
            decision=decision,
            checker_result=dict(data.get("checker_result") or {}),
            plugin_results=dict(data.get("plugin_results") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "reason": self.reason,
            "checker_result": dict(self.checker_result),
            "plugin_results": dict(self.plugin_results),
        }
        if self.event is not None:
            data["event"] = self.event.to_dict()
        if self.decision is not None:
            data["decision"] = self.decision.to_dict()
        return data

    def merged_with(self, incoming: "AuditTraceEntry") -> "AuditTraceEntry":
        checker_result = dict(self.checker_result)
        checker_result.update(incoming.checker_result)
        plugin_results = dict(self.plugin_results)
        plugin_results.update(incoming.plugin_results)
        return AuditTraceEntry(
            session_id=incoming.session_id or self.session_id,
            agent_id=incoming.agent_id or self.agent_id,
            user_id=incoming.user_id or self.user_id,
            reason=incoming.reason or self.reason,
            event=incoming.event or self.event,
            decision=incoming.decision or self.decision,
            checker_result=checker_result,
            plugin_results=plugin_results,
        )

    @property
    def event_id(self) -> str | None:
        return self.event.event_id if self.event is not None else None


class BaseAuditor:
    """Server-side trace auditor for a complete session trace."""

    name: str = "base"
    description: str = ""

    def audit(
        self,
        trace: list[AuditTraceEntry],
    ) -> AuditResult:
        raise NotImplementedError


def _runtime_event_from_trace_entry_data(data: dict[str, Any]) -> RuntimeEvent | None:
    event_data = data.get("event")
    if not isinstance(event_data, dict):
        checker_input = data.get("checker_input")
        if isinstance(checker_input, dict) and isinstance(checker_input.get("event"), dict):
            event_data = checker_input["event"]
        elif isinstance(data.get("event_type"), str):
            event_data = data
    if not isinstance(event_data, dict):
        return None
    try:
        return RuntimeEvent.from_dict(event_data)
    except Exception:
        return None


def _decision_from_trace_entry_data(data: dict[str, Any]) -> GuardDecision | None:
    decision_data = data.get("decision")
    if not isinstance(decision_data, dict):
        return None
    try:
        return GuardDecision.from_dict(decision_data)
    except Exception:
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
