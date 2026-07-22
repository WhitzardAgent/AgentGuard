"""Evidence-validated LLM agent security auditor."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from typing import Any

from backend.audit.agent_base import BaseLLMAgentAuditor
from backend.audit.agent_models import (
    AgentAuditContext,
    AgentAuditResult,
    AuditEvidence,
    AuditFinding,
    SessionAuditResult,
    SessionTrace,
    highest_severity,
)
from backend.audit.agent_registry import register
from backend.audit.finding_utils import semantic_dedupe_findings
from backend.audit.llm_client import AuditLLMClient
from backend.audit.prompts import aggregate_audit_prompt, session_audit_prompt
from shared.audit.redactor import redact

_VALID_LEVELS = {"critical", "high", "warning", "ok"}


@register(
    name="llm_agent_security",
    description="LLM-assisted semantic and cross-session security analysis.",
)
class LLMAgentSecurityAuditor(BaseLLMAgentAuditor):

    def __init__(
        self,
        client: AuditLLMClient | None = None,
        *,
        config: dict[str, Any] | None = None,
    ) -> None:
        explicit = dict(config or {})
        self.client = client or AuditLLMClient(config=explicit)
        self.chunk_size = max(
            1,
            min(
                int(
                    explicit.get("chunk_events")
                    or os.getenv("AGENTGUARD_AUDIT_LLM_CHUNK_EVENTS", "40")
                ),
                500,
            ),
        )

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> LLMAgentSecurityAuditor:
        return cls(config=config)

    def model_name(self) -> str | None:
        return self.client.model

    def audit(
        self,
        context: AgentAuditContext,
        sessions: Iterable[SessionTrace],
        *,
        deterministic_findings: list[AuditFinding] | None = None,
    ) -> AgentAuditResult:
        session_list = list(sessions)
        session_results = [self._audit_session(context, session) for session in session_list]
        event_scope: dict[str, set[str]] = {}
        for session in session_list:
            event_scope.setdefault(session.session_id, set()).update(session.event_ids())
        aggregate_payload = {
            "agent_id": context.agent_id,
            "coverage_manifest": {
                "snapshot_event_id": context.snapshot_event_id,
                "user_count": len({session.user_id for session in session_list if session.user_id}),
                "session_count": len(session_list),
                "trace_count": sum(len(session.entries) for session in session_list),
                "coverage": "all trace events in the selected stable agent snapshot",
            },
            "sessions": [_compact_session_result(item) for item in session_results],
            "deterministic_findings": [item.to_dict() for item in deterministic_findings or []],
        }
        aggregate = _parse_response(
            self.client.complete(aggregate_audit_prompt(aggregate_payload)),
            event_scope,
            source="llm_aggregate",
        )
        findings = semantic_dedupe_findings(
            [finding for item in session_results for finding in item.findings] + aggregate["findings"]
        )
        level = highest_severity(*(finding.severity for finding in findings), aggregate["level"])
        return AgentAuditResult(
            level=level,
            summary=aggregate["summary"] or f"LLM audit found {len(findings)} issue(s).",
            findings=findings,
            session_results=session_results,
            user_count=len({session.user_id for session in session_list if session.user_id}),
            session_count=len(session_list),
            trace_count=sum(len(session.entries) for session in session_list),
            metadata={"mode": "llm", "model": self.model_name(), "prompt_version": self.prompt_version},
        )

    def _audit_session(self, context: AgentAuditContext, session: SessionTrace) -> SessionAuditResult:
        event_scope = {session.session_id: session.event_ids()}
        findings: list[AuditFinding] = []
        summaries: list[str] = []
        levels: list[str] = []
        entries = [_safe_entry(entry.to_dict()) for entry in session.entries]
        for index in range(0, len(entries), self.chunk_size):
            chunk = entries[index:index + self.chunk_size]
            payload = {
                "agent_id": context.agent_id,
                "session_id": session.session_id,
                "user_id": session.user_id,
                "chunk_index": index // self.chunk_size,
                "chunk_count": max(1, (len(entries) + self.chunk_size - 1) // self.chunk_size),
                "session_trace_count": len(entries),
                "trace_entries": chunk,
            }
            parsed = _parse_response(
                self.client.complete(session_audit_prompt(payload)),
                event_scope,
                source="llm_session",
            )
            findings.extend(parsed["findings"])
            summaries.append(parsed["summary"])
            levels.append(parsed["level"])
        findings = semantic_dedupe_findings(findings)
        return SessionAuditResult(
            session_id=session.session_id,
            user_id=session.user_id,
            level=highest_severity(*(finding.severity for finding in findings), *levels),
            summary=" ".join(item for item in summaries if item)[:2000] or "No LLM issue detected.",
            findings=findings,
            trace_count=len(session.entries),
            metadata={"chunks": max(1, (len(entries) + self.chunk_size - 1) // self.chunk_size)},
        )


def _safe_entry(value: dict[str, Any]) -> dict[str, Any]:
    return redact(value)


def _parse_response(raw: str, event_scope: dict[str, set[str]], *, source: str) -> dict[str, Any]:
    payload = _json_object(raw)
    findings = []
    for item in payload.get("findings") or []:
        if not isinstance(item, dict):
            continue
        evidence = _validated_evidence(item.get("evidence"), event_scope)
        if not evidence:
            continue
        severity = str(item.get("severity") or "warning").lower()
        if severity not in _VALID_LEVELS - {"ok"}:
            severity = "warning"
        verification = str(item.get("verification") or "suspected").lower()
        if verification not in {"confirmed", "suspected"}:
            verification = "suspected"
        if verification == "suspected" and severity == "critical":
            severity = "high"
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        finding_id = f"finding_llm_{hashlib.sha256((source + signature).encode()).hexdigest()[:16]}"
        findings.append(AuditFinding(
            finding_id=finding_id,
            severity=severity,
            category=str(item.get("category") or "semantic_risk")[:128],
            title=str(item.get("title") or "LLM security finding")[:255],
            description=str(item.get("description") or "")[:4000],
            evidence=evidence,
            confidence=_confidence(item.get("confidence")),
            recommendation=str(item.get("recommendation") or "")[:2000],
            source=source,
            verification=verification,
        ))
    evidence_level = highest_severity(*(finding.severity for finding in findings))
    return {
        "level": evidence_level,
        "summary": str(payload.get("summary") or "")[:4000],
        "findings": findings,
    }


def _json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:].lstrip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM audit response is not JSON")
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM audit response must be a JSON object")
    return value


def _validated_evidence(value: Any, event_scope: dict[str, set[str]]) -> list[AuditEvidence]:
    if not isinstance(value, list):
        return []
    validated = []
    for item in value:
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("session_id") or "").strip()
        if session_id not in event_scope:
            continue
        event_ids = tuple(
            str(event_id) for event_id in item.get("event_ids") or []
            if str(event_id) in event_scope[session_id]
        )
        if not event_ids:
            continue
        validated.append(AuditEvidence(session_id=session_id, event_ids=event_ids))
    return validated


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.5


def _compact_session_result(result: SessionAuditResult) -> dict[str, Any]:
    return {
        "session_id": result.session_id,
        "user_id": result.user_id,
        "level": result.level,
        "summary": result.summary[:1500],
        "findings": [item.to_dict() for item in result.findings],
    }


__all__ = ["LLMAgentSecurityAuditor"]
