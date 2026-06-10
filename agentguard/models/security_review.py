"""Structured security review evidence shared across runtime surfaces."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ThreatType(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    COT_LEAK = "cot_leak"
    SKILL_SAFETY = "skill_safety"
    TRACE_ANOMALY = "trace_anomaly"


class ThreatSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {
            ThreatSeverity.NONE: 0,
            ThreatSeverity.LOW: 1,
            ThreatSeverity.MEDIUM: 2,
            ThreatSeverity.HIGH: 3,
            ThreatSeverity.CRITICAL: 4,
        }[self]


class ThreatFinding(BaseModel):
    threat_type: ThreatType
    severity: ThreatSeverity = ThreatSeverity.NONE
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]


class SecurityReviewResult(BaseModel):
    overall_severity: ThreatSeverity = ThreatSeverity.NONE
    findings: list[ThreatFinding] = Field(default_factory=list)
    summary: str = ""
    raw_response: str = ""

    def max_severity(self) -> ThreatSeverity:
        severity = self.overall_severity
        for finding in self.findings:
            if finding.severity.rank > severity.rank:
                severity = finding.severity
        return severity

    def threat_types(self) -> list[str]:
        seen: list[str] = []
        for finding in self.findings:
            value = finding.threat_type.value
            if value not in seen:
                seen.append(value)
        return seen

    def evidence_preview(self, *, limit: int = 2) -> list[str]:
        out: list[str] = []
        for finding in self.findings:
            for evidence in finding.evidence:
                text = _compact_text(evidence, max_len=96)
                if text and text not in out:
                    out.append(text)
                if len(out) >= limit:
                    return out
        return out

    @classmethod
    def unavailable(cls, reason: str) -> "SecurityReviewResult":
        return cls(
            overall_severity=ThreatSeverity.HIGH,
            findings=[],
            summary="security review unavailable",
            raw_response=reason,
        )


def _compact_text(value: Any, *, max_len: int = 512) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
