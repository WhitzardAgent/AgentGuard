"""Tests for the FastEvaluator (policy matcher)."""

from agentguard.models.decisions import Action
from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall
from agentguard.policy.dsl.compiler import compile_rules
from agentguard.policy.evaluator.matcher import FastEvaluator


def _ev(tool: str = "shell.exec", role: str = "basic", trust: int = 1, **kw):
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(agent_id="a", session_id="s", role=role, trust_level=trust),
        tool_call=ToolCall(tool_name=tool, args=kw.get("args", {}),
                           sink_type=kw.get("sink", "shell")),
    )


def test_allow_when_no_rules():
    ev = FastEvaluator()
    d = ev.evaluate(_ev())
    assert d.action == Action.ALLOW


def test_deny_matches():
    rules = compile_rules('''
    RULE: deny_shell
    ON: tool_call(shell.exec)
    CONDITION: principal.role == "basic"
    POLICY: DENY
    ''')
    ev = FastEvaluator(rules)
    d = ev.evaluate(_ev())
    assert d.action == Action.DENY
    assert "deny_shell" in d.matched_rules


def test_allow_not_matched():
    rules = compile_rules('''
    RULE: deny_shell
    ON: tool_call(shell.exec)
    CONDITION: principal.role == "basic"
    POLICY: DENY
    ''')
    ev = FastEvaluator(rules)
    d = ev.evaluate(_ev(role="admin"))
    assert d.action == Action.ALLOW


def test_deny_over_degrade():
    rules = compile_rules('''
    RULE: r1
    ON: tool_call(shell.exec)
    CONDITION: principal.role == "basic"
    POLICY: DEGRADE(shell.readonly)

    RULE: r2
    ON: tool_call(shell.exec)
    CONDITION: principal.trust_level < 2
    POLICY: DENY
    ''')
    ev = FastEvaluator(rules)
    d = ev.evaluate(_ev())
    assert d.action == Action.DENY


def test_no_tool_call_returns_allow():
    ev = FastEvaluator()
    event = RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(agent_id="a", session_id="s"),
    )
    d = ev.evaluate(event)
    assert d.action == Action.ALLOW


def test_wildcard_rules():
    rules = compile_rules('''
    RULE: global_check
    ON: tool_call(*)
    CONDITION: principal.trust_level == 0
    POLICY: HUMAN_CHECK
    ''')
    ev = FastEvaluator(rules)
    d = ev.evaluate(_ev(tool="anything.here", trust=0))
    assert d.action == Action.HUMAN_CHECK
