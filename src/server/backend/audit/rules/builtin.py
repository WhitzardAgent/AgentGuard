"""Built-in deterministic agent security audit rules."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from backend.audit.agent_models import (
    AgentAuditContext,
    AuditEvidence,
    AuditFinding,
    SessionTrace,
)
from backend.audit.rules.base import BaseAuditRule

_SENSITIVE_PATH_RE = re.compile(
    r"(?:^|[/\\])(?:\.env(?:\.[^/\\]+)?|id_(?:rsa|dsa|ecdsa|ed25519)|"
    r"shadow|passwd|credentials(?:\.[^/\\]+)?|authorized_keys|"
    r"secrets?(?:\.[^/\\]+)?|private[_-]?key(?:\.[^/\\]+)?)$",
    flags=re.IGNORECASE,
)
_SECRET_CONTENT_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)\s*[:=]\s*[^\s,;]{6,}|"
    r"\bAKIA[0-9A-Z]{16}\b",
    flags=re.IGNORECASE,
)
_OUTBOUND_TOOL_RE = re.compile(
    r"(?:send|email|mail|upload|webhook|http|request|post|publish|notify|external|network)",
    flags=re.IGNORECASE,
)


def _finding_id(rule_id: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join((rule_id, *parts)).encode()).hexdigest()[:16]
    return f"finding_{digest}"


def _decision(entry: Any) -> str | None:
    return entry.decision.decision_type.value if entry.decision is not None else None


def _event_type(entry: Any) -> str | None:
    return entry.event.event_type.value if entry.event is not None else None


def _tool_name(entry: Any) -> str | None:
    if entry.event is None:
        return None
    payload = entry.event.payload.to_dict()
    tool = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else payload
    value = tool.get("tool_name") or tool.get("name") if isinstance(tool, dict) else None
    return str(value).strip() if value else None


def _signals(entry: Any) -> set[str]:
    values: list[Any] = []
    if entry.event is not None:
        values.extend(entry.event.risk_signals or [])
    if entry.decision is not None:
        values.extend(entry.decision.risk_signals or [])
    if isinstance(entry.plugin_result, dict):
        values.extend(entry.plugin_result.get("risk_signals") or [])
    return {str(value) for value in values if value}


def _payload(entry: Any) -> dict[str, Any]:
    return entry.event.payload.to_dict() if entry.event is not None else {}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _strings(item)]
    return []


def _sensitive_paths(value: Any) -> list[str]:
    return [text for text in _strings(value) if _SENSITIVE_PATH_RE.search(text.strip())]


def _contains_secret(value: Any) -> bool:
    return any(_SECRET_CONTENT_RE.search(text) for text in _strings(value))


def _content_flows_to(source: Any, destination: Any) -> bool:
    source_values = [text.strip() for text in _strings(source) if len(text.strip()) >= 12]
    destination_values = [text.strip() for text in _strings(destination) if len(text.strip()) >= 12]
    return any(
        source_text in destination_text or destination_text in source_text
        for source_text in source_values
        for destination_text in destination_values
    )


class RepeatedDeniedActionRule(BaseAuditRule):
    rule_id = "AUDIT-SESSION-001"
    name = "Repeated denied actions"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for session in sessions:
            denied = [entry for entry in session.entries if _decision(entry) == "deny"]
            if len(denied) < 3:
                continue
            event_ids = tuple(entry.event_id for entry in denied if entry.event_id)
            findings.append(AuditFinding(
                finding_id=_finding_id(self.rule_id, session.session_id, session.user_id or ""),
                rule_id=self.rule_id,
                severity="high",
                category="policy_evasion",
                title="Repeated denied actions in one session",
                description=f"The session generated {len(denied)} denied actions, which may indicate repeated policy-evasion attempts.",
                evidence=[AuditEvidence(session.session_id, event_ids, session.user_id)],
                recommendation="Review the denied sequence and consider rate-limiting or closing the session after repeated violations.",
            ))
        return findings


class DeniedToolExecutedRule(BaseAuditRule):
    rule_id = "AUDIT-SESSION-005"
    name = "Denied tool still produced a result"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for session in sessions:
            denied_by_tool: dict[str, Any] = {}
            for entry in session.entries:
                tool = _tool_name(entry)
                if tool and _decision(entry) == "deny":
                    denied_by_tool[tool] = entry
                elif tool and _event_type(entry) == "tool_result" and tool in denied_by_tool:
                    denied = denied_by_tool[tool]
                    ids = tuple(value for value in (denied.event_id, entry.event_id) if value)
                    findings.append(AuditFinding(
                        finding_id=_finding_id(
                            self.rule_id, session.session_id, session.user_id or "", tool
                        ),
                        rule_id=self.rule_id,
                        severity="critical",
                        category="enforcement_bypass",
                        title="A denied tool call appears to have executed",
                        description=f"Tool '{tool}' produced a result after a deny decision in the same session.",
                        evidence=[AuditEvidence(session.session_id, ids, session.user_id)],
                        recommendation="Investigate the adapter enforcement path and prevent tool execution after a final deny decision.",
                    ))
        return findings


class SensitiveFileAccessRule(BaseAuditRule):
    rule_id = "AUDIT-DATA-001"
    name = "Potential sensitive file access"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for session in sessions:
            matched = [
                entry for entry in session.entries
                if _event_type(entry) == "tool_invoke"
                and _sensitive_paths(_payload(entry).get("arguments"))
            ]
            if not matched:
                continue
            findings.append(AuditFinding(
                finding_id=_finding_id(self.rule_id, session.session_id, session.user_id or ""),
                rule_id=self.rule_id,
                severity="warning",
                category="sensitive_file_access",
                title="A tool accessed a potentially sensitive file",
                description=(
                    f"{len(matched)} tool invocation(s) referenced a path commonly used for "
                    "credentials, secrets, account data, or private keys. Authorization is not "
                    "established by the trace alone."
                ),
                evidence=[AuditEvidence(
                    session.session_id,
                    tuple(entry.event_id for entry in matched if entry.event_id),
                    session.user_id,
                )],
                confidence=0.85,
                verification="suspected",
                recommendation=(
                    "Confirm that the user and agent were authorized to read the files, then "
                    "restrict file tools with path allowlists and least privilege."
                ),
            ))
        return findings


class SensitiveDataOutboundRule(BaseAuditRule):
    rule_id = "AUDIT-DATA-002"
    name = "Sensitive data sent through an outbound tool"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for session in sessions:
            sensitive_reads: dict[str, Any] = {}
            sensitive_results: list[tuple[Any, Any | None]] = []
            for entry in session.entries:
                event_type = _event_type(entry)
                tool = _tool_name(entry) or ""
                payload = _payload(entry)
                if event_type == "tool_invoke" and _sensitive_paths(payload.get("arguments")):
                    sensitive_reads[tool] = entry
                    continue
                if event_type == "tool_result":
                    source_invoke = sensitive_reads.get(tool)
                    if source_invoke is not None or _contains_secret(payload.get("result")):
                        sensitive_results.append((entry, source_invoke))
                    continue
                if event_type != "tool_invoke" or not _OUTBOUND_TOOL_RE.search(tool):
                    continue
                arguments = payload.get("arguments")
                for result_entry, source_invoke in sensitive_results:
                    result_value = _payload(result_entry).get("result")
                    if not _content_flows_to(result_value, arguments):
                        continue
                    evidence_ids = tuple(
                        item.event_id
                        for item in (source_invoke, result_entry, entry)
                        if item is not None and item.event_id
                    )
                    findings.append(AuditFinding(
                        finding_id=_finding_id(
                            self.rule_id,
                            session.session_id,
                            session.user_id or "",
                            result_entry.event_id or "",
                            entry.event_id or "",
                        ),
                        rule_id=self.rule_id,
                        severity="critical",
                        category="sensitive_data_exposure",
                        title="Sensitive data was passed to an outbound tool",
                        description=(
                            "Content obtained from a sensitive file or containing credential-like "
                            "material was subsequently included in an outbound tool invocation."
                        ),
                        evidence=[AuditEvidence(
                            session.session_id,
                            evidence_ids,
                            session.user_id,
                        )],
                        recommendation=(
                            "Revoke exposed credentials if applicable, block the destination, and "
                            "enforce data-flow controls between file/secret tools and outbound tools."
                        ),
                    ))
        return findings


class ThoughtAlignmentFailureAllowedRule(BaseAuditRule):
    rule_id = "AUDIT-SESSION-007"
    name = "Thought alignment failure allowed"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for session in sessions:
            matched = [
                entry for entry in session.entries
                if "thought_alignment_error" in _signals(entry)
                and (
                    (entry.decision and entry.decision.metadata.get("thought_alignment") == "error_allowed")
                    or entry.plugin_result.get("metadata", {}).get("thought_alignment") == "error_allowed"
                )
            ]
            if not matched:
                continue
            findings.append(AuditFinding(
                finding_id=_finding_id(self.rule_id, session.session_id, session.user_id or ""),
                rule_id=self.rule_id,
                severity="warning",
                category="alignment_failure",
                title="Thought alignment failed open",
                description=(
                    "Thought-Aligner was unavailable and the configured failure mode allowed the "
                    "original model output to continue. This is degraded control coverage, not by "
                    "itself proof of a successful attack."
                ),
                evidence=[AuditEvidence(session.session_id, tuple(item.event_id for item in matched if item.event_id), session.user_id)],
                recommendation="Use failure_mode=deny for sensitive agents and investigate the alignment endpoint failure.",
            ))
        return findings


class MissingToolDecisionRule(BaseAuditRule):
    rule_id = "AUDIT-AGENT-008"
    name = "Tool calls without guard decisions"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for session in sessions:
            missing = [
                entry for entry in session.entries
                if _event_type(entry) == "tool_invoke" and entry.decision is None
            ]
            if not missing:
                continue
            findings.append(AuditFinding(
                finding_id=_finding_id(self.rule_id, session.session_id, session.user_id or ""),
                rule_id=self.rule_id,
                severity="high",
                category="audit_gap",
                title="Tool calls are missing guard decisions",
                description=f"{len(missing)} tool invocation event(s) do not contain an AgentGuard decision.",
                evidence=[AuditEvidence(session.session_id, tuple(item.event_id for item in missing if item.event_id), session.user_id)],
                recommendation="Verify adapter coverage and trace persistence for the tool_before phase.",
            ))
        return findings


class ConcentratedRiskToolRule(BaseAuditRule):
    rule_id = "AUDIT-AGENT-004"
    name = "Concentrated risky tool usage"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        by_tool: dict[str, list[tuple[SessionTrace, Any]]] = defaultdict(list)
        for session in sessions:
            for entry in session.entries:
                tool = _tool_name(entry)
                if tool and (_decision(entry) == "deny" or _signals(entry)):
                    by_tool[tool].append((session, entry))
        findings: list[AuditFinding] = []
        for tool, matches in by_tool.items():
            if len(matches) < 3:
                continue
            evidence = [
                AuditEvidence(session.session_id, (entry.event_id,) if entry.event_id else (), session.user_id)
                for session, entry in matches[:20]
            ]
            findings.append(AuditFinding(
                finding_id=_finding_id(self.rule_id, tool),
                rule_id=self.rule_id,
                severity="warning",
                category="risk_concentration",
                title="Risk is concentrated around one tool",
                description=f"Tool '{tool}' is associated with {len(matches)} risky or denied event(s).",
                evidence=evidence,
                recommendation="Review this tool's labels, capabilities and policy coverage.",
            ))
        return findings


class CrossUserSessionRule(BaseAuditRule):
    rule_id = "AUDIT-AGENT-007"
    name = "Session identifiers shared across users"

    def evaluate(self, context: AgentAuditContext, sessions: list[SessionTrace]) -> list[AuditFinding]:
        users_by_session: dict[str, set[str]] = defaultdict(set)
        for session in sessions:
            if session.user_id:
                users_by_session[session.session_id].add(session.user_id)
        findings: list[AuditFinding] = []
        for session_id, users in users_by_session.items():
            if len(users) < 2:
                continue
            findings.append(AuditFinding(
                finding_id=_finding_id(self.rule_id, session_id),
                rule_id=self.rule_id,
                severity="critical",
                category="identity_boundary",
                title="A session identifier is associated with multiple users",
                description=f"Session '{session_id}' is associated with {len(users)} users.",
                evidence=[AuditEvidence(session_id, (), user_id) for user_id in sorted(users)],
                recommendation="Close the affected session and investigate runtime identity/session mapping.",
            ))
        return findings


def builtin_audit_rules() -> list[BaseAuditRule]:
    return [
        RepeatedDeniedActionRule(),
        DeniedToolExecutedRule(),
        SensitiveFileAccessRule(),
        SensitiveDataOutboundRule(),
        ThoughtAlignmentFailureAllowedRule(),
        MissingToolDecisionRule(),
        ConcentratedRiskToolRule(),
        CrossUserSessionRule(),
    ]


__all__ = ["builtin_audit_rules"]
