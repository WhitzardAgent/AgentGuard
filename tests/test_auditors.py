from __future__ import annotations

import pytest

from fastapi import HTTPException

from backend.api import frontend_router
from backend.audit import AuditTraceEntry, auditor_manager
from backend.api.schemas import TraceAuditRequest
from backend.runtime.manager import RuntimeManager


def test_runtime_manager_persists_trace_window_and_current_event():
    manager = RuntimeManager()
    manager.decide(
        {
            "request_id": "audit-trace",
            "context": {
                "session_id": "audit-session",
                "agent_id": "audit-agent",
                "user_id": "audit-user",
            },
            "current_event": {
                "event_id": "evt-current",
                "event_type": "llm_input",
                "payload": {"messages": [{"role": "user", "content": "hello"}]},
                "risk_signals": [],
            },
            "trajectory_window": [
                {
                    "event_id": "evt-previous",
                    "event_type": "tool_result",
                    "context": {
                        "session_id": "audit-session",
                        "agent_id": "audit-agent",
                        "user_id": "audit-user",
                    },
                    "payload": {"tool_name": "read_file", "result": "ok"},
                    "risk_signals": [],
                }
            ],
            "local_signals": [],
        }
    )

    trace = manager.get_trace_records(
        "audit-session",
        agent_id="audit-agent",
        user_id="audit-user",
    )

    event_ids = {
        record.event.event_id
        for record in trace
        if record.event is not None
    }

    assert event_ids == {"evt-previous", "evt-current"}
    current = next(record for record in trace if record.event is not None and record.event.event_id == "evt-current")
    assert current.decision is not None
    assert current.decision.decision_type.value == "allow"


def test_frontend_router_runs_custom_trace_audit(monkeypatch):
    manager = RuntimeManager()
    manager.record_uploaded_trace(
        {
            "session_id": "audit-session",
            "agent_id": "audit-agent",
            "user_id": "audit-user",
            "reason": "unit-test",
            "entries": [
                {
                    "event": {
                        "event_id": "evt-secret",
                        "event_type": "tool_result",
                        "context": {
                            "session_id": "audit-session",
                            "agent_id": "audit-agent",
                            "user_id": "audit-user",
                        },
                        "payload": {
                            "tool_name": "read_file",
                            "result": "API_KEY=sk-ABCDEFGH12345678",
                        },
                        "risk_signals": ["secret_detected", "api_key_detected"],
                    },
                    "decision": {
                        "decision_type": "deny",
                        "reason": "blocked because a secret was observed",
                    },
                }
            ],
        }
    )
    monkeypatch.setattr(frontend_router, "_manager", manager)

    payload = frontend_router.run_custom_trace_audit(
        TraceAuditRequest(
            session_id="audit-session",
            agent_id="audit-agent",
            user_id="audit-user",
            auditor_name="trace_risk_summary",
        )
    )

    assert payload.level == "critical"
    assert payload.trace_entries == 1
    assert "secret_detected" in payload.reason
    assert payload.metadata["decision_counts"]["deny"] == 1


def test_frontend_router_rejects_unknown_auditor(monkeypatch):
    manager = RuntimeManager()
    manager.record_uploaded_trace(
        {
            "session_id": "audit-session",
            "entries": [
                {
                    "event": {
                        "event_id": "evt-1",
                        "event_type": "llm_input",
                        "context": {"session_id": "audit-session"},
                        "payload": {"messages": [{"role": "user", "content": "hello"}]},
                        "risk_signals": [],
                    }
                }
            ],
        }
    )
    monkeypatch.setattr(frontend_router, "_manager", manager)

    with pytest.raises(HTTPException) as exc:
        frontend_router.run_custom_trace_audit(
            TraceAuditRequest(
                session_id="audit-session",
                auditor_name="missing_auditor",
            )
        )

    assert exc.value.status_code == 400


def test_frontend_router_lists_registered_auditors():
    payload = frontend_router.list_auditors()

    assert payload["auditors"]
    assert any(item["name"] == "trace_risk_summary" for item in payload["auditors"])
    summary = next(item for item in payload["auditors"] if item["name"] == "trace_risk_summary")
    assert "Summarize a full trace" in summary["description"]


def test_auditor_manager_uses_trace_only():
    result = auditor_manager().audit(
        "trace_risk_summary",
        [
            AuditTraceEntry.from_dict(
                {
                    "event": {
                        "event_id": "evt-1",
                        "event_type": "tool_result",
                        "context": {
                            "session_id": "audit-session",
                            "agent_id": "audit-agent",
                            "user_id": "audit-user",
                        },
                        "payload": {"tool_name": "read_file", "result": "ok"},
                        "risk_signals": [],
                    },
                    "decision": {"decision_type": "deny", "reason": "blocked"},
                }
            )
        ],
    )

    assert result.level == "critical"
    assert result.metadata["session_ids"] == ["audit-session"]
    assert result.metadata["agent_ids"] == ["audit-agent"]
    assert result.metadata["user_ids"] == ["audit-user"]
