from __future__ import annotations

from backend.console.dsl import parse_source
from shared.schemas.context import RuntimeContext
from shared.schemas.events import RuntimeEvent
from shared.schemas.policy import PolicyEffect, PolicyRule, RuleCondition, TraceClause
from shared.rules.trace_pattern import TraceStep, match_trace


def _tool_invoke(tool_name: str, arguments: dict[str, object] | None = None) -> RuntimeEvent:
    return RuntimeEvent.from_dict(
        {
            "event_type": "tool_invoke",
            "context": {"session_id": "s1", "agent_id": "a1", "user_id": "u1"},
            "payload": {
                "tool_name": tool_name,
                "arguments": arguments or {},
                "capabilities": [],
            },
            "metadata": {},
        }
    )


def test_trace_pattern_optional_gap_requires_tail():
    assert not match_trace("A -> ...? -> C", ["A"])
    assert match_trace("A -> ...? -> C", ["A", "C"])
    assert match_trace("A -> ...? -> C", ["A", "B", "C"])


def test_console_parser_compiles_trace_clause():
    parsed, report = parse_source(
        "RULE: external_to_email_review\n"
        "TRACE: A -> ...? -> C\n"
        'CONDITION: A.boundary == "internal"\n'
        '  AND C.name == "email_send"\n'
        "POLICY: DENY\n"
        'Reason: "DENY for *"\n'
    )

    assert report.ok
    assert len(parsed) == 1
    rule = parsed[0].rule
    assert rule.trace_clause is not None
    assert [step.name for step in rule.trace_clause.steps] == ["A", "C"]
    assert rule.trace_clause.steps[1].sep == "-> ...?"


def test_trace_rule_does_not_match_single_lookup_without_email_tail():
    rule = PolicyRule(
        rule_id="external_to_email_review",
        effect=PolicyEffect.DENY,
        reason="DENY for *",
        priority=90,
        event_types=["tool_invoke"],
        conditions=[
            RuleCondition(field="A.boundary", op="eq", value="internal"),
            RuleCondition(field="C.name", op="eq", value="email_send"),
        ],
        trace_clause=TraceClause(
            steps=[
                TraceStep(name="A", sep=""),
                TraceStep(name="C", sep="-> ...?"),
            ]
        ),
    )

    current = _tool_invoke("erp_orders_lookup", {"company": "ACME", "period": "last_week"})
    current.metadata["tool_labels"] = {"boundary": "internal"}

    assert rule.matches(current, trace_window=[]) is False


def test_trace_rule_matches_lookup_then_email_send():
    rule = PolicyRule(
        rule_id="external_to_email_review",
        effect=PolicyEffect.DENY,
        reason="DENY for *",
        priority=90,
        event_types=["tool_invoke"],
        conditions=[
            RuleCondition(field="A.boundary", op="eq", value="internal"),
            RuleCondition(field="C.name", op="eq", value="email_send"),
        ],
        trace_clause=TraceClause(
            steps=[
                TraceStep(name="A", sep=""),
                TraceStep(name="C", sep="-> ...?"),
            ]
        ),
    )

    previous = _tool_invoke("erp_orders_lookup", {"company": "ACME", "period": "last_week"})
    previous.metadata["tool_labels"] = {"boundary": "internal"}
    current = _tool_invoke("email_send", {"to": "partner@example.com"})

    assert rule.matches(current, trace_window=[previous]) is True


def test_trace_rule_supports_boolean_condition_expression():
    rule = PolicyRule(
        rule_id="boolean_trace_review",
        effect=PolicyEffect.DENY,
        reason="boolean condition review",
        priority=90,
        event_types=["tool_invoke"],
        conditions=[
            RuleCondition(field="A.sensitivity", op="eq", value="high"),
            RuleCondition(field="principal.trust_level", op="lt", value=2),
            RuleCondition(field="C.name", op="eq", value="email_send"),
        ],
        condition_expr='(A.sensitivity == "high" OR principal.trust_level < 2) AND C.name == "email_send"',
        trace_clause=TraceClause(
            steps=[
                TraceStep(name="A", sep=""),
                TraceStep(name="C", sep="-> ...?"),
            ]
        ),
    )

    previous = _tool_invoke("erp_orders_lookup", {"company": "ACME"})
    previous.metadata["tool_labels"] = {"sensitivity": "low"}
    current = _tool_invoke("email_send", {"to": "partner@example.com"})
    current.context = RuntimeContext(session_id="s1", agent_id="a1", user_id="u1", metadata={"trust_level": 1})

    assert rule.matches(current, trace_window=[previous]) is True


def test_trace_rule_supports_not_expression():
    rule = PolicyRule(
        rule_id="negated_trace_rule",
        effect=PolicyEffect.DENY,
        reason="negated condition review",
        priority=90,
        event_types=["tool_invoke"],
        conditions=[
            RuleCondition(field="C.name", op="eq", value="email_send"),
            RuleCondition(field="principal.role", op="eq", value="system"),
        ],
        condition_expr='C.name == "email_send" AND NOT principal.role == "system"',
        trace_clause=TraceClause(
            steps=[
                TraceStep(name="A", sep=""),
                TraceStep(name="C", sep="-> ...?"),
            ]
        ),
    )

    previous = _tool_invoke("erp_orders_lookup", {"company": "ACME"})
    current = _tool_invoke("email_send", {"to": "partner@example.com"})
    current.context = RuntimeContext(session_id="s1", agent_id="a1", user_id="u1", metadata={"role": "basic"})

    assert rule.matches(current, trace_window=[previous]) is True
