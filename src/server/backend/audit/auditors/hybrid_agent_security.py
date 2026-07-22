"""Hybrid deterministic and LLM agent security auditor."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.audit.agent_base import BaseAgentAuditor
from backend.audit.agent_models import (
    AgentAuditContext,
    AgentAuditResult,
    SessionAuditResult,
    SessionTrace,
    highest_severity,
)
from backend.audit.agent_registry import register
from backend.audit.auditors.llm_agent_security import LLMAgentSecurityAuditor
from backend.audit.auditors.rule_agent_security import RuleBasedAgentSecurityAuditor
from backend.audit.finding_utils import semantic_dedupe_findings
from backend.audit.llm_client import LLMAuditUnavailable


@register(
    name="hybrid_agent_security",
    description="Deterministic rules plus evidence-validated LLM analysis.",
)
class HybridAgentSecurityAuditor(BaseAgentAuditor):

    def __init__(
        self,
        rule_auditor: RuleBasedAgentSecurityAuditor | None = None,
        llm_auditor: LLMAgentSecurityAuditor | None = None,
    ) -> None:
        self.rule_auditor = rule_auditor or RuleBasedAgentSecurityAuditor()
        self.llm_auditor = llm_auditor or LLMAgentSecurityAuditor()

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> HybridAgentSecurityAuditor:
        return cls(llm_auditor=LLMAgentSecurityAuditor.from_config(config))

    def audit(self, context: AgentAuditContext, sessions: Iterable[SessionTrace]) -> AgentAuditResult:
        session_list = list(sessions)
        deterministic = self.rule_auditor.audit(context, session_list)
        if not session_list:
            deterministic.metadata.update({"mode": "hybrid", "llm_status": "skipped", "model": self.llm_auditor.model_name()})
            deterministic.limitations.append("No trace events were present in the selected audit snapshot.")
            return deterministic
        limitations: list[str] = []
        try:
            semantic = self.llm_auditor.audit(
                context,
                session_list,
                deterministic_findings=deterministic.findings,
            )
            llm_status = "completed"
        except (LLMAuditUnavailable, ValueError) as exc:
            semantic = AgentAuditResult()
            llm_status = "unavailable"
            limitations.append(str(exc))
        findings = semantic_dedupe_findings([
            *deterministic.findings,
            *semantic.findings,
        ])
        level = highest_severity(*(item.severity for item in findings))
        summary = deterministic.summary
        if llm_status == "completed" and semantic.summary:
            summary = f"{semantic.summary} Deterministic checks found {len(deterministic.findings)} issue(s)."
        return AgentAuditResult(
            level=level,
            summary=summary,
            findings=findings,
            session_results=_merge_session_results(
                deterministic.session_results,
                semantic.session_results,
            ),
            user_count=deterministic.user_count,
            session_count=deterministic.session_count,
            trace_count=deterministic.trace_count,
            limitations=limitations,
            metadata={
                "mode": "hybrid",
                "llm_status": llm_status,
                "model": self.llm_auditor.model_name(),
                "rules_evaluated": deterministic.metadata.get("rules_evaluated", []),
            },
        )


def _merge_session_results(
    deterministic: list[SessionAuditResult],
    semantic: list[SessionAuditResult],
) -> list[SessionAuditResult]:
    semantic_by_key = {(item.session_id, item.user_id): item for item in semantic}
    merged = []
    for rule_result in deterministic:
        key = (rule_result.session_id, rule_result.user_id)
        model_result = semantic_by_key.pop(key, None)
        if model_result is None:
            merged.append(rule_result)
            continue
        findings = semantic_dedupe_findings([
            *rule_result.findings,
            *model_result.findings,
        ])
        merged.append(SessionAuditResult(
            session_id=rule_result.session_id,
            user_id=rule_result.user_id,
            level=highest_severity(*(finding.severity for finding in findings)),
            summary=f"{model_result.summary} {rule_result.summary}".strip(),
            findings=findings,
            trace_count=max(rule_result.trace_count, model_result.trace_count),
            metadata={**rule_result.metadata, **model_result.metadata},
        ))
    merged.extend(semantic_by_key.values())
    return merged


__all__ = ["HybridAgentSecurityAuditor"]
