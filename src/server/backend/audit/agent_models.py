"""Typed models for agent-wide security audits."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from backend.audit.base import AuditTraceEntry

AuditSeverity = Literal["critical", "high", "warning", "ok"]
FindingVerification = Literal["confirmed", "suspected"]

_SEVERITY_RANK = {"ok": 0, "warning": 1, "high": 2, "critical": 3}


def highest_severity(*values: str) -> AuditSeverity:
    normalized = [value for value in values if value in _SEVERITY_RANK]
    return max(normalized, key=_SEVERITY_RANK.__getitem__) if normalized else "ok"


@dataclass(frozen=True)
class AgentAuditContext:
    run_id: str
    agent_id: str
    requested_by_user_id: int
    start_at: datetime | None = None
    end_at: datetime | None = None
    snapshot_event_id: int | None = None
    auditor_name: str = "hybrid_agent_security"
    model: str | None = None


@dataclass
class SessionTrace:
    session_id: str
    user_id: str | None
    entries: list[AuditTraceEntry] = field(default_factory=list)

    def event_ids(self) -> set[str]:
        return {entry.event_id for entry in self.entries if entry.event_id}


@dataclass(frozen=True)
class AuditEvidence:
    session_id: str
    event_ids: tuple[str, ...] = ()
    user_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "event_ids": list(self.event_ids),
            "user_id": self.user_id,
        }


@dataclass
class AuditFinding:
    finding_id: str
    severity: AuditSeverity
    category: str
    title: str
    description: str
    evidence: list[AuditEvidence] = field(default_factory=list)
    confidence: float = 1.0
    recommendation: str = ""
    source: str = "rule"
    rule_id: str | None = None
    verification: FindingVerification = "confirmed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence": max(0.0, min(float(self.confidence), 1.0)),
            "recommendation": self.recommendation,
            "source": self.source,
            "rule_id": self.rule_id,
            "verification": self.verification,
        }


@dataclass
class SessionAuditResult:
    session_id: str
    user_id: str | None
    level: AuditSeverity = "ok"
    summary: str = "No issue detected in session."
    findings: list[AuditFinding] = field(default_factory=list)
    trace_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "level": self.level,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "trace_count": self.trace_count,
            "metadata": dict(self.metadata),
        }


@dataclass
class AgentAuditResult:
    level: AuditSeverity = "ok"
    summary: str = "No issue detected for agent."
    findings: list[AuditFinding] = field(default_factory=list)
    session_results: list[SessionAuditResult] = field(default_factory=list)
    user_count: int = 0
    session_count: int = 0
    trace_count: int = 0
    limitations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_sessions: bool = False) -> dict[str, Any]:
        payload = {
            "level": self.level,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "user_count": self.user_count,
            "session_count": self.session_count,
            "trace_count": self.trace_count,
            "limitations": list(self.limitations),
            "metadata": dict(self.metadata),
        }
        if include_sessions:
            payload["session_results"] = [item.to_dict() for item in self.session_results]
        return payload


__all__ = [
    "AgentAuditContext",
    "AgentAuditResult",
    "AuditEvidence",
    "AuditFinding",
    "AuditSeverity",
    "FindingVerification",
    "SessionAuditResult",
    "SessionTrace",
    "highest_severity",
]
