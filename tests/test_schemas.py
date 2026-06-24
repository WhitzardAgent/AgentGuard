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


def test_human_check_roundtrip_and_legacy_compatibility():
    decision = GuardDecision.human_check("needs review")
    restored = GuardDecision.from_dict(decision.to_dict())
    legacy = GuardDecision.from_dict({"decision_type": "ask_user", "reason": "legacy review"})

    assert restored.decision_type == DecisionType.HUMAN_CHECK
    assert restored.requires_user is True
    assert legacy.decision_type == DecisionType.HUMAN_CHECK
    assert legacy.to_dict()["decision_type"] == "human_check"


def test_llm_output_supports_thought_and_final_output_roundtrip():
    ctx = RuntimeContext(session_id="s")
    event = ev.llm_output(
        ctx,
        {"thought": "internal chain", "final_output": "visible answer"},
    )

    assert event.payload.output == "visible answer"
    assert event.payload.thought == "internal chain"
    assert event.payload.final_output == "visible answer"

    restored = ev.RuntimeEvent.from_dict(event.to_dict())
    assert restored.payload.output == "visible answer"
    assert restored.payload.thought == "internal chain"
    assert restored.payload.final_output == "visible answer"


def test_llm_output_preserves_unstructured_dict_as_output_text():
    ctx = RuntimeContext(session_id="s")
    event = ev.llm_output(ctx, {"tool_calls": [{"name": "search"}]})

    assert "tool_calls" in event.payload.output
    assert event.payload.thought is None
    assert event.payload.final_output is None


def test_llm_output_aliases_fill_specific_fields():
    ctx = RuntimeContext(session_id="s")
    thought_event = ev.llm_thought(ctx, "internal chain")
    final_event = ev.final_response(ctx, "visible answer")

    assert thought_event.payload.output == "internal chain"
    assert thought_event.payload.thought == "internal chain"
    assert thought_event.payload.final_output is None

    assert final_event.payload.output == "visible answer"
    assert final_event.payload.thought is None
    assert final_event.payload.final_output == "visible answer"
