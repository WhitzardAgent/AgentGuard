"""Risk assessment model produced by middleware analyzers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> "RiskLevel":
        if score >= 0.9:
            return cls.CRITICAL
        if score >= 0.7:
            return cls.HIGH
        if score >= 0.4:
            return cls.MODERATE
        if score > 0.0:
            return cls.LOW
        return cls.NONE


class RiskAssessment(BaseModel):
    """Aggregated risk signal attached to an event by the middleware chain."""

    score: float = 0.0
    level: RiskLevel = RiskLevel.NONE
    categories: list[str] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)

    def add(self, category: str, score: float, **signals: Any) -> "RiskAssessment":
        self.categories.append(category)
        self.score = max(self.score, min(1.0, score))
        self.level = RiskLevel.from_score(self.score)
        if signals:
            self.signals[category] = signals
        return self
