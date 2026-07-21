"""Finding normalization shared by rule, session-LLM, and aggregate audits."""
from __future__ import annotations

from backend.audit.agent_models import AuditFinding

_SOURCE_RANK = {"rule": 0, "llm_session": 1, "llm_aggregate": 2}
_SEVERITY_RANK = {"critical": 0, "high": 1, "warning": 2, "ok": 3}


def semantic_dedupe_findings(findings: list[AuditFinding]) -> list[AuditFinding]:
    """Prefer primary incidents and suppress duplicates or covered precursor risks."""
    exact = {finding.finding_id: finding for finding in findings}
    ordered = sorted(
        exact.values(),
        key=lambda item: (
            _SOURCE_RANK.get(item.source, 9),
            _SEVERITY_RANK.get(item.severity, 9),
            item.finding_id,
        ),
    )
    kept: list[AuditFinding] = []
    for candidate in ordered:
        if any(_same_risk(candidate, existing) for existing in kept):
            continue
        kept.append(candidate)
    return [
        candidate
        for candidate in kept
        if not any(
            candidate is not primary and _covered_by(candidate, primary)
            for primary in kept
        )
    ]


def _same_risk(left: AuditFinding, right: AuditFinding) -> bool:
    if _risk_family(left.category) != _risk_family(right.category):
        return False
    return _evidence_is_covered(left, right) or _evidence_is_covered(right, left)


def _covered_by(candidate: AuditFinding, primary: AuditFinding) -> bool:
    """Return whether a stronger incident already contains this subordinate observation."""
    if primary.verification != "confirmed" or primary.severity not in {"critical", "high"}:
        return False
    primary_family = _risk_family(primary.category)
    candidate_family = _risk_family(candidate.category)
    if primary_family != "sensitive_data_exposure":
        return False
    is_precursor = candidate_family == "sensitive_file_access"
    is_llm_control_observation = (
        candidate_family == "security_control_failure"
        and candidate.source in {"llm_session", "llm_aggregate"}
    )
    if not (is_precursor or is_llm_control_observation):
        return False
    return _evidence_is_covered(candidate, primary)


def _evidence_is_covered(candidate: AuditFinding, primary: AuditFinding) -> bool:
    candidate_evidence = _event_evidence(candidate)
    primary_evidence = _event_evidence(primary)
    if not candidate_evidence or not primary_evidence:
        return False
    return all(
        any(_same_evidence_event(item, owner) for owner in primary_evidence)
        for item in candidate_evidence
    )


def _same_evidence_event(
    left: tuple[str, str | None, str],
    right: tuple[str, str | None, str],
) -> bool:
    left_session, left_user, left_event = left
    right_session, right_user, right_event = right
    if left_session != right_session or left_event != right_event:
        return False
    return left_user is None or right_user is None or left_user == right_user


def _event_evidence(finding: AuditFinding) -> frozenset[tuple[str, str | None, str]]:
    return frozenset(
        (evidence.session_id, evidence.user_id, event_id)
        for evidence in finding.evidence
        for event_id in evidence.event_ids
    )


def _risk_family(category: str) -> str:
    normalized = str(category or "other").lower()
    if "alignment" in normalized:
        return "alignment"
    if "sensitive_file" in normalized or "file_access" in normalized:
        return "sensitive_file_access"
    if any(token in normalized for token in ("exfil", "data_exposure", "credential_exposure", "unsafe_tool")):
        return "sensitive_data_exposure"
    if any(token in normalized for token in ("enforcement", "control", "policy_absence", "plugin_misconfiguration", "audit_gap")):
        return "security_control_failure"
    if any(token in normalized for token in ("identity", "cross_user", "privilege")):
        return "identity_boundary"
    if any(token in normalized for token in ("policy_evasion", "policy_bypass")):
        return "policy_bypass"
    return normalized


__all__ = ["semantic_dedupe_findings"]
