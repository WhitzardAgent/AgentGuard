from __future__ import annotations

import json
from collections.abc import Iterable

from backend.audit import AuditTraceEntry, agent_registry
from backend.audit.agent_base import BaseAgentAuditor
from backend.audit.agent_manager import AgentAuditorManager
from backend.audit.agent_models import (
    AgentAuditContext,
    AgentAuditResult,
    AuditEvidence,
    AuditFinding,
    SessionTrace,
)
from backend.audit.agent_store import AgentAuditStore
from backend.audit.auditors.hybrid_agent_security import HybridAgentSecurityAuditor
from backend.audit.auditors.llm_agent_security import LLMAgentSecurityAuditor
from backend.audit.auditors.rule_agent_security import RuleBasedAgentSecurityAuditor
from backend.audit.finding_utils import semantic_dedupe_findings
from backend.audit.llm_client import LLMAuditUnavailable
from backend.audit.prompts import aggregate_audit_prompt, session_audit_prompt


def _denied_entry(index: int) -> AuditTraceEntry:
    return AuditTraceEntry.from_dict(
        {
            "session_id": "session-a",
            "agent_id": "agent-a",
            "user_id": "7",
            "event": {
                "event_id": f"evt-{index}",
                "event_type": "tool_invoke",
                "context": {"session_id": "session-a", "agent_id": "agent-a", "user_id": "7"},
                "payload": {"tool_name": "send_email", "arguments": {"to": "outside@example.com"}},
                "risk_signals": [],
            },
            "decision": {"decision_type": "deny", "reason": "blocked"},
        }
    )


def test_rule_agent_auditor_detects_repeated_denied_actions() -> None:
    session = SessionTrace("session-a", "7", [_denied_entry(index) for index in range(3)])

    result = RuleBasedAgentSecurityAuditor().audit(
        AgentAuditContext("run-a", "agent-a", 1),
        [session],
    )

    assert result.level == "high"
    assert result.user_count == 1
    assert result.session_count == 1
    assert any(item.rule_id == "AUDIT-SESSION-001" for item in result.findings)


class FakeLLMClient:
    model = "audit-test-model"

    def complete(self, prompt: str, *, max_tokens: int = 4096) -> str:
        if '"trace_entries"' in prompt:
            return json.dumps(
                {
                    "risk_level": "critical",
                    "summary": "One valid and one forged finding.",
                    "findings": [
                        {
                            "severity": "high",
                            "category": "tool_misuse",
                            "title": "Valid finding",
                            "description": "Backed by a real event.",
                            "confidence": 0.8,
                            "evidence": [{"session_id": "session-a", "event_ids": ["evt-0"]}],
                            "recommendation": "Review the call.",
                        },
                        {
                            "severity": "critical",
                            "category": "invented",
                            "title": "Forged finding",
                            "description": "References an event that does not exist.",
                            "confidence": 1,
                            "evidence": [{"session_id": "session-a", "event_ids": ["evt-missing"]}],
                            "recommendation": "Ignore.",
                        },
                    ],
                }
            )
        return json.dumps({"risk_level": "ok", "summary": "No additional cross-session issue.", "findings": []})


def test_llm_agent_auditor_rejects_forged_evidence() -> None:
    session = SessionTrace("session-a", "7", [_denied_entry(0)])

    result = LLMAgentSecurityAuditor(client=FakeLLMClient()).audit(
        AgentAuditContext("run-a", "agent-a", 1),
        [session],
    )

    assert result.level == "high"
    assert [item.title for item in result.findings] == ["Valid finding"]
    assert result.findings[0].evidence[0].event_ids == ("evt-0",)
    assert result.findings[0].verification == "suspected"


class UnavailableLLMAuditor:
    def model_name(self):
        return "unavailable-model"

    def audit(self, *args, **kwargs):
        raise LLMAuditUnavailable("model unavailable")


def test_hybrid_auditor_keeps_rule_findings_when_llm_is_unavailable() -> None:
    session = SessionTrace("session-a", "7", [_denied_entry(index) for index in range(3)])
    auditor = HybridAgentSecurityAuditor(llm_auditor=UnavailableLLMAuditor())  # type: ignore[arg-type]

    result = auditor.audit(AgentAuditContext("run-a", "agent-a", 1), [session])

    assert result.level == "high"
    assert result.metadata["llm_status"] == "unavailable"
    assert any(item.rule_id == "AUDIT-SESSION-001" for item in result.findings)


def test_hybrid_auditor_merges_rule_and_model_session_findings() -> None:
    session = SessionTrace("session-a", "7", [_denied_entry(index) for index in range(3)])
    auditor = HybridAgentSecurityAuditor(
        llm_auditor=LLMAgentSecurityAuditor(client=FakeLLMClient()),
    )

    result = auditor.audit(AgentAuditContext("run-a", "agent-a", 1), [session])

    session_findings = result.session_results[0].findings
    assert any(item.rule_id == "AUDIT-SESSION-001" for item in session_findings)
    assert any(item.source == "llm_session" for item in session_findings)


def test_rule_auditor_keeps_same_session_id_separate_for_each_user() -> None:
    sessions = [
        SessionTrace("shared-session", "7", [_denied_entry(0)]),
        SessionTrace("shared-session", "8", [_denied_entry(1)]),
    ]

    result = RuleBasedAgentSecurityAuditor().audit(
        AgentAuditContext("run-a", "agent-a", 1),
        sessions,
    )

    assert result.level == "critical"
    assert len(result.session_results) == 2
    assert {item.user_id for item in result.session_results} == {"7", "8"}
    assert all(
        any(finding.rule_id == "AUDIT-AGENT-007" for finding in item.findings)
        for item in result.session_results
    )


def test_interrupted_audits_are_failed_on_server_recovery() -> None:
    class FakeDatabase:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql
            return 2

    database = FakeDatabase()

    recovered = AgentAuditStore(db=database).recover_interrupted_runs()  # type: ignore[arg-type]

    assert recovered == 2
    assert "WHERE status IN ('queued', 'running')" in database.sql
    assert "Server restarted before the audit completed" in database.sql


def test_sensitive_file_content_sent_outbound_is_confirmed_critical() -> None:
    def entry(event_id: str, event_type: str, tool_name: str, payload: dict) -> AuditTraceEntry:
        body = {"tool_name": tool_name, **payload}
        return AuditTraceEntry.from_dict({
            "session_id": "session-secret",
            "agent_id": "agent-a",
            "user_id": "7",
            "event": {
                "event_id": event_id,
                "event_type": event_type,
                "context": {"session_id": "session-secret", "agent_id": "agent-a", "user_id": "7"},
                "payload": body,
                "risk_signals": [],
            },
            "decision": {"decision_type": "allow", "reason": "test"},
        })

    session = SessionTrace("session-secret", "7", [
        entry("evt-read", "tool_invoke", "read_file", {"arguments": {"path": "/app/.env"}}),
        entry("evt-secret", "tool_result", "read_file", {"result": "API_KEY=supersecretvalue"}),
        entry("evt-send", "tool_invoke", "send_webhook", {"arguments": {"body": "API_KEY=supersecretvalue"}}),
    ])

    result = RuleBasedAgentSecurityAuditor().audit(
        AgentAuditContext("run-a", "agent-a", 1),
        [session],
    )

    assert [item.rule_id for item in result.findings] == ["AUDIT-DATA-002"]
    outbound = result.findings[0]
    assert outbound.severity == "critical"
    assert outbound.verification == "confirmed"
    assert outbound.evidence[0].event_ids == ("evt-read", "evt-secret", "evt-send")
    assert [item.rule_id for item in result.session_results[0].findings] == ["AUDIT-DATA-002"]


def test_sensitive_file_access_remains_when_no_outbound_flow_exists() -> None:
    entry = AuditTraceEntry.from_dict({
        "session_id": "session-read-only",
        "agent_id": "agent-a",
        "user_id": "7",
        "event": {
            "event_id": "evt-read-only",
            "event_type": "tool_invoke",
            "context": {
                "session_id": "session-read-only",
                "agent_id": "agent-a",
                "user_id": "7",
            },
            "payload": {"tool_name": "read_file", "arguments": {"path": "/app/.env"}},
            "risk_signals": [],
        },
        "decision": {"decision_type": "allow", "reason": "test"},
    })

    result = RuleBasedAgentSecurityAuditor().audit(
        AgentAuditContext("run-a", "agent-a", 1),
        [SessionTrace("session-read-only", "7", [entry])],
    )

    assert [item.rule_id for item in result.findings] == ["AUDIT-DATA-001"]
    assert result.findings[0].verification == "suspected"


def test_llm_control_observation_is_covered_by_confirmed_exposure() -> None:
    exposure = AuditFinding(
        finding_id="finding-exposure",
        severity="critical",
        category="sensitive_data_exposure",
        title="Sensitive data was sent",
        description="Confirmed flow.",
        evidence=[AuditEvidence("session-a", ("evt-read", "evt-send"), "7")],
        source="rule",
        verification="confirmed",
    )
    control = AuditFinding(
        finding_id="finding-control",
        severity="high",
        category="security_control_failure",
        title="Default allow despite a secret signal",
        description="No final plugin decision.",
        evidence=[AuditEvidence("session-a", ("evt-send",))],
        source="llm_session",
        verification="confirmed",
    )

    assert semantic_dedupe_findings([control, exposure]) == [exposure]


def test_multi_user_risk_chains_are_deduplicated_independently() -> None:
    def entry(
        user_id: str,
        session_id: str,
        event_id: str,
        event_type: str,
        tool_name: str,
        payload: dict,
    ) -> AuditTraceEntry:
        return AuditTraceEntry.from_dict({
            "session_id": session_id,
            "agent_id": "agent-a",
            "user_id": user_id,
            "event": {
                "event_id": event_id,
                "event_type": event_type,
                "context": {
                    "session_id": session_id,
                    "agent_id": "agent-a",
                    "user_id": user_id,
                },
                "payload": {"tool_name": tool_name, **payload},
                "risk_signals": [],
            },
            "decision": {"decision_type": "allow", "reason": "test"},
        })

    user_7 = SessionTrace("session-user-7", "7", [
        entry("7", "session-user-7", "evt-7-read", "tool_invoke", "read_file", {"arguments": {"path": "/app/.env"}}),
        entry("7", "session-user-7", "evt-7-result", "tool_result", "read_file", {"result": "API_KEY=user7secret"}),
        entry("7", "session-user-7", "evt-7-send", "tool_invoke", "send_webhook", {"arguments": {"body": "API_KEY=user7secret"}}),
    ])
    user_8 = SessionTrace("session-user-8", "8", [
        entry("8", "session-user-8", "evt-8-read", "tool_invoke", "read_file", {"arguments": {"path": "/app/.env"}}),
    ])

    result = RuleBasedAgentSecurityAuditor().audit(
        AgentAuditContext("run-a", "agent-a", 1),
        [user_7, user_8],
    )

    assert result.user_count == 2
    assert result.session_count == 2
    assert {(item.rule_id, item.evidence[0].user_id) for item in result.findings} == {
        ("AUDIT-DATA-002", "7"),
        ("AUDIT-DATA-001", "8"),
    }
    by_user = {item.user_id: item for item in result.session_results}
    assert [item.rule_id for item in by_user["7"].findings] == ["AUDIT-DATA-002"]
    assert [item.rule_id for item in by_user["8"].findings] == ["AUDIT-DATA-001"]


def test_semantic_dedupe_does_not_merge_explicitly_different_users() -> None:
    findings = [
        AuditFinding(
            finding_id=f"finding-user-{user_id}",
            severity="warning",
            category="sensitive_file_access",
            title="Sensitive file access",
            description="Same trace coordinates but a different explicit user.",
            evidence=[AuditEvidence("shared-session", ("shared-event",), user_id)],
            verification="suspected",
        )
        for user_id in ("7", "8")
    ]

    assert semantic_dedupe_findings(findings) == findings


def test_semantic_dedupe_prefers_rule_for_same_risk_and_evidence() -> None:
    rule = RuleBasedAgentSecurityAuditor().audit(
        AgentAuditContext("run-a", "agent-a", 1),
        [SessionTrace("session-a", "7", [_denied_entry(index) for index in range(3)])],
    ).findings[0]
    duplicate = type(rule)(
        finding_id="finding-llm-copy",
        severity="critical",
        category="policy_bypass",
        title="Model restatement",
        description="Same denied sequence.",
        evidence=rule.evidence,
        source="llm_aggregate",
        verification="suspected",
    )

    findings = semantic_dedupe_findings([duplicate, rule])

    assert findings == [rule]


def test_audit_prompts_define_strict_positive_and_negative_examples() -> None:
    session_prompt = session_audit_prompt({"trace_entries": []})
    aggregate_prompt = aggregate_audit_prompt({"sessions": []})

    assert "confirmed sensitive-file exfiltration" in session_prompt
    assert "authorized recipient is not exfiltration" in session_prompt
    assert "admin@example.com" in session_prompt
    assert "A suspected risk must not be critical" in session_prompt
    assert "duplicate session finding" in aggregate_prompt
    assert '"verification": "confirmed|suspected"' in session_prompt


def test_agent_auditor_manager_applies_per_run_llm_config() -> None:
    auditor = AgentAuditorManager().get(
        "llm_agent_security",
        llm_config={
            "base_url": "https://audit-model.example/v1",
            "model": "audit-model",
            "api_key": "test-only-key",
            "timeout_s": 75,
            "chunk_events": 23,
        },
    )

    assert isinstance(auditor, LLMAgentSecurityAuditor)
    assert auditor.model_name() == "audit-model"
    assert auditor.chunk_size == 23
    assert auditor.client.provider.base_url == "https://audit-model.example/v1"
    assert auditor.client.provider.timeout_s == 75


def test_agent_auditor_manager_uses_registered_custom_auditor(monkeypatch) -> None:
    agent_registry.discover_agent_auditors()

    class RegisteredAgentAuditor(BaseAgentAuditor):
        def audit(
            self,
            context: AgentAuditContext,
            sessions: Iterable[SessionTrace],
        ) -> AgentAuditResult:
            return AgentAuditResult(summary=f"Audited {context.agent_id}.")

    monkeypatch.setitem(
        agent_registry._AGENT_AUDITORS,
        "registered_test_agent_auditor",
        RegisteredAgentAuditor,
    )
    monkeypatch.setitem(
        agent_registry._DESCRIPTIONS,
        "registered_test_agent_auditor",
        "Test-only registered agent auditor.",
    )

    auditor = AgentAuditorManager().get("registered_test_agent_auditor")

    assert isinstance(auditor, RegisteredAgentAuditor)
    assert any(
        item["name"] == "registered_test_agent_auditor"
        for item in AgentAuditorManager.descriptions()
    )


def test_security_audit_catalog_is_admin_only_via_dependency_override() -> None:
    from backend.api.app import create_app
    from backend.api.auth import configured_backend_api_key
    from backend.api.authz import require_admin_user
    from backend.user.store import User
    from fastapi.testclient import TestClient

    app = create_app()
    app.dependency_overrides[require_admin_user] = lambda: User(
        id=1,
        username="admin",
        profile_json='{"role":"admin"}',
    )
    client = TestClient(app)

    headers = {"X-Api-Key": configured_backend_api_key()} if configured_backend_api_key() else {}
    response = client.get("/v1/backend/security-audits/auditors", headers=headers)

    assert response.status_code == 200
    assert {item["name"] for item in response.json()["auditors"]} == {
        "hybrid_agent_security",
        "llm_agent_security",
        "rule_agent_security",
    }
