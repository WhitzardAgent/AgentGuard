"""Tests for data models."""

from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall, ProvenanceRef
from agentguard.models.decisions import Action, Decision, Obligation


def test_event_creation():
    ev = RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(agent_id="a", session_id="s"),
        tool_call=ToolCall(tool_name="shell.exec", args={"cmd": "ls"}, sink_type="shell"),
    )
    assert ev.event_type == EventType.TOOL_CALL_ATTEMPT
    assert ev.tool_call.tool_name == "shell.exec"
    assert ev.event_id  # auto generated


def test_runtime_event_model_validate_json_preserves_principal_user_id():
    raw = """
    {
      "event_type": "tool_call_attempt",
      "principal": {
        "agent_id": "agent-a",
        "session_id": "sess-a",
        "user_id": "user-123"
      },
      "tool_call": {
        "tool_name": "shell.exec",
        "args": {"cmd": "ls"},
        "sink_type": "shell"
      }
    }
    """
    ev = RuntimeEvent.model_validate_json(raw)
    assert ev.principal.user_id == "user-123"


def test_decision_allow():
    d = Decision.allow(reason="no-match")
    assert d.action == Action.ALLOW
    assert d.reason == "no-match"


def test_action_priority():
    assert Action.DENY.priority < Action.HUMAN_CHECK.priority
    assert Action.HUMAN_CHECK.priority < Action.DEGRADE.priority
    assert Action.DEGRADE.priority < Action.ALLOW.priority


def test_event_with_tool_call():
    ev = RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(agent_id="a", session_id="s"),
        tool_call=ToolCall(tool_name="old", args={}),
    )
    new_tc = ToolCall(tool_name="new", args={"x": 1})
    ev2 = ev.with_tool_call(new_tc)
    assert ev2.tool_call.tool_name == "new"
    assert ev.tool_call.tool_name == "old"  # immutability


def test_provenance_ref():
    ref = ProvenanceRef(node_id="r1", label="pii/ssn", confidence=0.99)
    assert ref.label == "pii/ssn"
