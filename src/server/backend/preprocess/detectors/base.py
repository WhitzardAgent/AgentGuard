"""Detector base and result type."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DetectionResult:
    object_id: str
    object_type: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    risk_labels: list[str] = field(default_factory=list)
    policy_targets: list[str] = field(default_factory=list)
    suggested_plugins: list[str] = field(default_factory=list)
    risk_level: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "object_type": self.object_type,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "risk_labels": list(self.risk_labels),
            "policy_targets": list(self.policy_targets),
            "suggested_plugins": list(self.suggested_plugins),
            "risk_level": self.risk_level,
            "metadata": self.metadata,
        }


class BaseDetector:
    object_type: str = "object"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        raise NotImplementedError
