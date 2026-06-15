"""Manager for registered auditors."""
from __future__ import annotations

from backend.audit.base import AuditResult, BaseAuditor
from backend.audit.registry import get_auditor_class


class AuditorManager:
    def __init__(self, auditors: list[BaseAuditor] | None = None) -> None:
        self._auditors: dict[str, BaseAuditor] = {
            auditor.name: auditor for auditor in (auditors or [])
        }

    def get(self, name: str) -> BaseAuditor:
        auditor = self._auditors.get(name)
        if auditor is not None:
            return auditor
        auditor_class = get_auditor_class(name)
        if auditor_class is None:
            raise ValueError(f"unknown auditor: {name}")
        auditor = auditor_class()
        self._auditors[name] = auditor
        return auditor

    def audit(
        self,
        auditor_name: str,
        trace: list[dict[str, object]],
        *,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> AuditResult:
        auditor = self.get(auditor_name)
        return auditor.audit(
            trace,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
        )


def auditor_manager() -> AuditorManager:
    return AuditorManager()


# Backward-compatible aliases for older imports.
CustomAuditorManager = AuditorManager
custom_auditor_manager = auditor_manager
