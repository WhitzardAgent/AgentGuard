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
    label: str = ""
    reason: str = ""
    agent_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    skill_unique_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
        if self.label:
            payload["label"] = self.label
        if self.reason:
            payload["reason"] = self.reason
        if self.agent_id is not None:
            payload["agent_id"] = self.agent_id
        if self.user_id is not None:
            payload["user_id"] = self.user_id
        if self.session_id is not None:
            payload["session_id"] = self.session_id
        if self.skill_unique_id is not None:
            payload["skill_unique_id"] = self.skill_unique_id
        return payload

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        object_id: str = "",
        object_type: str = "object",
        name: str = "",
    ) -> DetectionResult | None:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return None
        normalized_object_id = str(
            value.get("object_id")
            or value.get("skill_unique_id")
            or value.get("id")
            or object_id
            or name
            or ""
        )
        normalized_name = str(value.get("name") or name or normalized_object_id)
        return cls(
            object_id=normalized_object_id,
            object_type=str(value.get("object_type") or object_type),
            name=normalized_name,
            capabilities=_string_list(value.get("capabilities")),
            risk_labels=_string_list(value.get("risk_labels")),
            policy_targets=_string_list(value.get("policy_targets")),
            suggested_plugins=_string_list(value.get("suggested_plugins")),
            risk_level=str(value.get("risk_level") or "unknown"),
            label=str(value.get("label") or value.get("result") or ""),
            reason=str(value.get("reason") or ""),
            agent_id=_optional_string(value.get("agent_id")),
            user_id=_optional_string(value.get("user_id")),
            session_id=_optional_string(value.get("session_id")),
            skill_unique_id=_optional_string(value.get("skill_unique_id")),
            metadata=dict(value.get("metadata") or {}),
        )


class BaseDetector:
    object_type: str = "object"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        raise NotImplementedError


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
