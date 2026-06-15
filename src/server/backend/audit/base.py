"""Base auditor interface and normalized audit result."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

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


class BaseAuditor:
    """Server-side trace auditor for a complete session trace."""

    name: str = "base"
    description: str = ""

    def audit(
        self,
        trace: list[dict[str, Any]],
        *,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> AuditResult:
        raise NotImplementedError
