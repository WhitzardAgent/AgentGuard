"""Deterministic agent-wide security auditor."""
from __future__ import annotations

from collections.abc import Iterable

from backend.audit.agent_base import BaseAgentAuditor
from backend.audit.agent_models import (
    AgentAuditContext,
    AgentAuditResult,
    SessionAuditResult,
    SessionTrace,
    highest_severity,
)
from backend.audit.finding_utils import semantic_dedupe_findings
from backend.audit.rules import BaseAuditRule, builtin_audit_rules


class RuleBasedAgentSecurityAuditor(BaseAgentAuditor):
    name = "rule_agent_security"
    description = "Deterministic cross-session and cross-user security checks."

    def __init__(self, rules: list[BaseAuditRule] | None = None) -> None:
        self.rules = list(rules or builtin_audit_rules())

    def audit(self, context: AgentAuditContext, sessions: Iterable[SessionTrace]) -> AgentAuditResult:
        session_list = list(sessions)
        findings = []
        for rule in self.rules:
            findings.extend(rule.evaluate(context, session_list))
        by_session: dict[tuple[str, str | None], list] = {
            (session.session_id, session.user_id): [] for session in session_list
        }
        for finding in findings:
            for evidence in finding.evidence:
                key = (evidence.session_id, evidence.user_id)
                if evidence.user_id is None:
                    for session in session_list:
                        if session.session_id == evidence.session_id:
                            by_session.setdefault((session.session_id, session.user_id), []).append(finding)
                    continue
                by_session.setdefault(key, []).append(finding)
        session_results = []
        for session in session_list:
            current = semantic_dedupe_findings(
                by_session.get((session.session_id, session.user_id), [])
            )
            level = highest_severity(*(finding.severity for finding in current))
            session_results.append(SessionAuditResult(
                session_id=session.session_id,
                user_id=session.user_id,
                level=level,
                summary=f"Deterministic audit found {len(current)} issue(s)." if current else "No deterministic issue detected.",
                findings=current,
                trace_count=len(session.entries),
            ))
        findings = semantic_dedupe_findings(findings)
        level = highest_severity(*(finding.severity for finding in findings))
        return AgentAuditResult(
            level=level,
            summary=f"Deterministic audit found {len(findings)} issue(s) across {len(session_list)} session(s).",
            findings=findings,
            session_results=session_results,
            user_count=len({session.user_id for session in session_list if session.user_id}),
            session_count=len(session_list),
            trace_count=sum(len(session.entries) for session in session_list),
            metadata={"rules_evaluated": [rule.rule_id for rule in self.rules], "mode": "rule"},
        )

__all__ = ["RuleBasedAgentSecurityAuditor"]
