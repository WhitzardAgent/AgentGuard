"""Cross-boundary remote guard protocol messages."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RemoteGuardRequest:
    """POST /v1/server/guard/decide request body."""

    current_event: dict[str, Any]
    context: dict[str, Any]
    request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    trajectory_window: list[dict[str, Any]] = field(default_factory=list)
    local_signals: list[str] = field(default_factory=list)
    policy_version: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "current_event": self.current_event,
            "context": self.context,
            "trajectory_window": self.trajectory_window,
            "local_signals": list(self.local_signals),
            "policy_version": self.policy_version,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteGuardRequest":
        return cls(
            current_event=dict(data.get("current_event") or {}),
            context=dict(data.get("context") or {}),
            request_id=data.get("request_id") or f"req_{uuid.uuid4().hex[:12]}",
            trajectory_window=list(data.get("trajectory_window") or []),
            local_signals=list(data.get("local_signals") or []),
            policy_version=data.get("policy_version"),
            extensions=dict(data.get("extensions") or {}),
        )


@dataclass
class RemoteGuardResponse:
    """POST /v1/server/guard/decide response body."""

    decision: dict[str, Any]
    risk_signals: list[str] = field(default_factory=list)
    checker_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_signals": list(self.risk_signals),
            "checker_result": self.checker_result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteGuardResponse":
        return cls(
            decision=dict(data.get("decision") or {}),
            risk_signals=list(data.get("risk_signals") or []),
            checker_result=dict(data.get("checker_result") or {}),
        )
