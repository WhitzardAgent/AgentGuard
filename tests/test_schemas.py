from __future__ import annotations

from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision


def test_event_redaction_strips_secrets():
    ctx = RuntimeContext(session_id="s")
    e = ev.tool_result(ctx, "read", "token sk-ABCDEFGH12345678 here")
    red = e.redacted()
    assert "sk-ABCDEFGH12345678" not in str(red.payload)
    assert "[REDACTED]" in str(red.payload)
    # original is untouched
    assert "sk-ABCDEFGH12345678" in str(e.payload)


def test_event_stable_hash_ignores_volatile_fields():
    ctx = RuntimeContext(session_id="s")
    a = ev.tool_invoke(ctx, "t", {"x": 1}, capabilities=["read_file"])
    b = ev.tool_invoke(ctx, "t", {"x": 1}, capabilities=["read_file"])
    assert a.event_id != b.event_id
    assert a.stable_hash() == b.stable_hash()


def test_decision_roundtrip_and_properties():
    d = GuardDecision.require_approval("needs human")
    assert d.requires_user is True
    assert d.is_blocking is True
    restored = GuardDecision.from_dict(d.to_dict())
    assert restored.decision_type == DecisionType.REQUIRE_APPROVAL
    assert restored.reason == "needs human"
