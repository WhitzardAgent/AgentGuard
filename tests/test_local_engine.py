from __future__ import annotations

from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType
from agentguard.u_guard.local_engine import LocalGuardEngine
from agentguard.u_guard.policy_snapshot import PolicySnapshot


def _engine():
    return LocalGuardEngine(PolicySnapshot.default())


def test_default_allow_is_certain_without_signals():
    e = ev.tool_invoke(RuntimeContext(session_id="s"), "read_file", {"path": "/tmp"}, capabilities=["read_file"])
    result = _engine().evaluate(e)
    assert result.decision.decision_type == DecisionType.ALLOW
    assert result.certain is True


def test_default_allow_uncertain_with_signals():
    e = ev.tool_invoke(RuntimeContext(session_id="s"), "noop", {}, capabilities=[])
    e.add_signal("some_unmatched_signal")
    result = _engine().evaluate(e)
    if result.decision.decision_type == DecisionType.ALLOW:
        assert result.certain is False


def test_external_send_escalates():
    e = ev.tool_invoke(
        RuntimeContext(session_id="s"), "send_email", {"to": "x@y.com"}, capabilities=["external_send"]
    )
    result = _engine().evaluate(e)
    assert result.decision.decision_type in (
        DecisionType.REQUIRE_REMOTE_REVIEW,
        DecisionType.DENY,
    )
