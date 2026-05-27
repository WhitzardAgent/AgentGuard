"""Tests for the DSL parser (v3 syntax only)."""

import pytest
from agentguard.policy.dsl.parser import parse_rules, parse_rule_source
from agentguard.policy.dsl.ast import RuleAST, Compare, BoolOp, NotOp, ExistsPath, Path, SetLit


SIMPLE_RULE = """
RULE: deny_shell_basic
ON:        tool_call(shell.exec)
CONDITION: principal.role == "basic"
POLICY:    DENY
"""

TWO_RULES = """
RULE: r1
ON:        tool_call(email.send)
CONDITION: target.domain == "evil.com"
POLICY:    DENY

RULE: r2
ON:        tool_call(*)
CONDITION: principal.trust_level < 2
POLICY:    HUMAN_CHECK
"""

DEGRADE_RULE = """
RULE: degrade_email
ON:        tool_call(email.send)
CONDITION: principal.trust_level == 1
POLICY:    DEGRADE(email.send_to_draft)
"""

EXISTS_PATH_RULE = """
RULE: deny_pii_to_email
ON:        tool_call(email.send)
CONDITION: EXISTS_PATH(source_label IN {"pii", "pii/*"}, max_hops = 4)
POLICY:    DENY
"""

COMPLEX_EXPR = """
RULE: complex
ON:        tool_call(shell.*)
CONDITION: (principal.role == "admin" OR principal.trust_level > 2)
           AND NOT target.domain == "safe.local"
POLICY:    ALLOW
"""


def test_simple_rule():
    rules = parse_rule_source(SIMPLE_RULE)
    assert len(rules) == 1
    r = rules[0]
    assert r.rule_id == "deny_shell_basic"
    assert r.tool_pattern == "shell.exec"
    assert r.action.kind == "DENY"
    assert isinstance(r.expr, Compare)
    assert str(r.expr.path) == "principal.role"
    assert r.expr.op == "=="
    assert r.expr.value == "basic"


def test_two_rules():
    rules = parse_rules(TWO_RULES)
    assert len(rules) == 2
    assert rules[0].rule_id == "r1"
    assert rules[1].tool_pattern == "*"


def test_degrade():
    rules = parse_rule_source(DEGRADE_RULE)
    assert len(rules) == 1
    assert rules[0].action.kind == "DEGRADE"
    assert rules[0].action.profile == "email.send_to_draft"


def test_exists_path():
    rules = parse_rule_source(EXISTS_PATH_RULE)
    assert len(rules) == 1
    expr = rules[0].expr
    assert isinstance(expr, ExistsPath)
    assert expr.source_labels == ["pii", "pii/*"]
    assert expr.max_hops == 4


def test_complex_bool():
    rules = parse_rule_source(COMPLEX_EXPR)
    r = rules[0]
    assert isinstance(r.expr, BoolOp)
    assert r.expr.op == "AND"


def test_in_operator():
    dsl = """
    RULE: r_in
    ON:        tool_call(browser.open)
    CONDITION: target.domain IN {"evil.com", "bad.org"}
    POLICY:    DENY
    """
    rules = parse_rule_source(dsl)
    assert isinstance(rules[0].expr, Compare)
    assert rules[0].expr.op == "IN"
    assert isinstance(rules[0].expr.value, SetLit)


def test_not_in_operator():
    dsl = """
    RULE: r_not_in
    ON:        tool_call(email.send)
    CONDITION: target.domain NOT IN {"safe.com"}
    POLICY:    HUMAN_CHECK
    """
    rules = parse_rule_source(dsl)
    assert rules[0].expr.op == "NOT_IN"


def test_v1_v2_syntax_raises():
    """Old v1/v2 syntax must now raise a parse error."""
    from agentguard.models.errors import RuleCompileError
    old_style = """
    RULE deny_shell
    ON tool_call(shell.exec)
    IF principal.role == "basic"
    THEN DENY
    """
    with pytest.raises(RuleCompileError):
        parse_rule_source(old_style)
