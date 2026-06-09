"""AgentDoG diagnosis schema (three-dimensional safety taxonomy)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentDoGDiagnosis:
    risk_score: float
    risk_level: str
    source_labels: list[str] = field(default_factory=list)  # Risk Source
    failure_mode_labels: list[str] = field(default_factory=list)  # Failure Mode
    consequence_labels: list[str] = field(default_factory=list)  # Real-world Harm
    unsafe_event_ids: list[str] = field(default_factory=list)
    root_cause: str | None = None
    explanation: str | None = None
    decision_hint: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "source_labels": list(self.source_labels),
            "failure_mode_labels": list(self.failure_mode_labels),
            "consequence_labels": list(self.consequence_labels),
            "unsafe_event_ids": list(self.unsafe_event_ids),
            "root_cause": self.root_cause,
            "explanation": self.explanation,
            "decision_hint": self.decision_hint,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }
