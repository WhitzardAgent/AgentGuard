"""Tests for the rule compiler."""

from agentguard.models.decisions import Action
from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall
from agentguard.policy.dsl.compiler import compile_rules, RuleCompiler
from agentguard.policy.dsl.parser import parse_rule_source


def _event(tool: str = "shell.exec", role: str = "basic", trust: int = 1, **kw):
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(agent_id="a", session_id="s", role=role, trust_level=trust),
        tool_call=ToolCall(tool_name=tool, args=kw.get("args", {}), sink_type=kw.get("sink", "none")),
    )


def test_simple_deny():
    rules = compile_rules('''
    RULE: deny_shell
    ON: tool_call(shell.exec)
    CONDITION: principal.role == "basic"
    POLICY: DENY
    ''')
    assert len(rules) == 1
    r = rules[0]
    assert r.action == Action.DENY
    assert r.matches_tool("shell.exec")
    assert not r.matches_tool("email.send")
    assert r.predicate(_event("shell.exec", "basic"), {})
    assert not r.predicate(_event("shell.exec", "admin"), {})


def test_wildcard_tool():
    rules = compile_rules('''
    RULE: deny_all_basic
    ON: tool_call(*)
    CONDITION: principal.trust_level < 1
    POLICY: DENY
    ''')
    r = rules[0]
    assert r.matches_tool("anything")
    assert r.predicate(_event(trust=0), {})
    assert not r.predicate(_event(trust=1), {})


def test_degrade_compile():
    rules = compile_rules('''
    RULE: degrade_email
    ON: tool_call(email.send)
    CONDITION: principal.trust_level == 1
    POLICY: DEGRADE(email.send_to_draft)
    ''')
    assert rules[0].action == Action.DEGRADE
    assert rules[0].degrade_profile == "email.send_to_draft"


def test_compile_preserves_source():
    src = '''
    RULE: r1
    ON: tool_call(shell.exec)
    CONDITION: principal.role == "basic"
    POLICY: DENY
    '''
    rules = compile_rules(src)
    assert rules[0].source == src
