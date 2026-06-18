"""Tests for the management-console state and DSL bridge (offline)."""
from __future__ import annotations

from backend.console.dsl import parse_source, policy_rule_to_source
from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager

_DENY_RULE = (
    "RULE: block_shell\n"
    "ON: tool_call.requested(shell.exec)\n"
    'CONDITION: A.name == "shell.exec"\n'
    "POLICY: DENY\n"
    'Reason: "no shell"'
)


def _console() -> ConsoleState:
    return ConsoleState(
        RuntimeManager(
            plugin_config={
                "phases": {
                    "tool_before": {"client": [], "server": ["tool_invoke", "rule_based_plugin"]}
                }
            }
        )
    )


def test_dsl_parse_and_roundtrip():
    parsed, report = parse_source(_DENY_RULE)
    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.rule_id == "block_shell"
    assert rule.tool_names == ["shell.exec"]
    source = policy_rule_to_source(rule)
    assert "RULE: block_shell" in source
    assert "POLICY: DENY" in source


def test_check_reports_missing_lines():
    result = _console().check("RULE: x\nPOLICY: DENY")
    assert result["ok"] is False
    assert any("CONDITION" in e["message"] for e in result["errors"])


def test_publish_list_delete_rule():
    con = _console()
    before = len(con.list_rules())
    res = con.publish_rule("agent-alpha", _DENY_RULE)
    assert res["ok"] is True and res["rule_id"] == "block_shell"

    rules = con.list_rules("agent-alpha")
    managed = [r for r in rules if r["user_managed"]]
    assert any(r["rule_id"] == "block_shell" for r in managed)
    # Published rule is available to the optional rule-based checker.
    assert any(r.rule_id == "block_shell" for r in con.manager.policy.store.rules())

    dup = con.publish_rule("agent-alpha", _DENY_RULE)
    assert dup["ok"] is False  # duplicate id

    deleted = con.delete_rule("agent-alpha", "block_shell")
    assert deleted["ok"] is True
    assert len(con.list_rules()) == before


def test_observer_records_traffic_audit_and_tickets():
    con = _console()
    mgr = con.manager
    # deny via exfiltration
    mgr.decide({
        "context": {"session_id": "s1", "agent_id": "agent-alpha"},
        "current_event": {
            "event_type": "tool_invoke",
            "payload": {"tool_name": "send_email", "capabilities": ["external_send"]},
        },
        "trajectory_window": [{
            "event_type": "tool_result",
            "payload": {"tool_name": "file.read", "result": "sk-ABCD1234secret"},
            "risk_signals": ["secret_detected"],
        }],
    })
    # held decision -> approval ticket
    mgr.decide({
        "context": {"session_id": "s2", "agent_id": "agent-alpha"},
        "current_event": {
            "event_type": "tool_invoke",
            "payload": {"tool_name": "payments.charge", "capabilities": ["payment"]},
        },
        "trajectory_window": [],
    })

    traffic = con.traffic("agent-alpha")
    assert len(traffic) == 2
    assert any(e["action"] == "deny" for e in traffic)
    assert len(con.audit_recent("agent-alpha")) == 2

    tickets = con.approvals("agent-alpha")
    assert len(tickets) == 1
    tid = tickets[0]["ticket_id"]
    assert con.resolve_ticket(tid, approved=True) is True
    assert con.approvals("agent-alpha") == []


def test_health_reports_rule_counts():
    con = _console()
    health = con.health()
    assert health["ok"] is True
    assert health["rules"] >= 1
    assert "rule_version" in health


def test_register_tool_adds_or_updates_console_catalog():
    con = _console()
    tool = con.register_tool(
        {"agent_id": "live-agent"},
        {
            "name": "docs.search",
            "input_params": ["query"],
            "labels": {
                "boundary": "internal",
                "sensitivity": "moderate",
                "integrity": "trusted",
                "tags": ["read_only"],
            },
        },
    )
    assert tool is not None
    assert tool["owner_agent_id"] == "live-agent"
    assert tool["name"] == "docs.search"
    assert tool["input_params"] == ["query"]

    scoped = con.tools("live-agent")
    assert any(item["name"] == "docs.search" for item in scoped)
