"""Tests for DSL runtime features using v3 syntax.

Covers:
  - ON event subtypes: requested / completed / failed
  - Path aliases (caller.*, tool.*, event.*)
  - Function-style predicates
  - exists_path with source.label IN {...}
  - Bare semantic signals (goal_drift_detected())
  - DEGRADE profile
  - Rule metadata (Severity / Category / Reason)
  - Action-level obligations (WITH REDACT / AUDIT)
"""

from __future__ import annotations

import pytest

from agentguard.models.decisions import Action
from agentguard.models.events import (
    EventType, Principal, ProvenanceRef, RuntimeEvent, ToolCall,
)
from agentguard.policy.dsl.ast import BareFunc, FuncCall, ObligationAST
from agentguard.policy.dsl.compiler import compile_rules
from agentguard.policy.dsl.parser import parse_rule_source
from agentguard.runtime.dispatcher import set_session_signal, clear_session_signals


def _ev(tool: str = "send_email", role: str = "planner", trust: int = 1,
        target: dict | None = None, args: dict | None = None,
        scope: list[str] | None = None, extra: dict | None = None,
        session_id: str = "s-test",
        event_type: EventType = EventType.TOOL_CALL_REQUESTED) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=event_type,
        principal=Principal(agent_id="a", session_id=session_id,
                            role=role, trust_level=trust),
        tool_call=ToolCall(tool_name=tool, args=args or {},
                           target=target or {}, sink_type="email"),
        scope=scope or [],
        extra=extra or {},
    )


# ---------------------------------------------------------------- Event subtype

def test_event_subtype_requested():
    rules = compile_rules("""
    RULE: r_req
    ON:        tool_call.requested
    CONDITION: tool.name == "http_post"
    POLICY:    DENY
    """)
    r = rules[0]
    assert r.event_subtype == "requested"
    assert r.tool_pattern == "*"
    ev_req = _ev("http_post", event_type=EventType.TOOL_CALL_REQUESTED)
    assert r.predicate(ev_req, {})


def test_event_subtype_filters_in_evaluator():
    from agentguard.policy.evaluator.matcher import FastEvaluator
    rules = compile_rules("""
    RULE: only_on_completed
    ON:        tool_call.completed
    CONDITION: tool.name == "x"
    POLICY:    DENY
    """)
    ev = FastEvaluator(rules)
    d_req = ev.evaluate(_ev("x", event_type=EventType.TOOL_CALL_REQUESTED))
    assert d_req.action == Action.ALLOW
    d_done = ev.evaluate(_ev("x", event_type=EventType.TOOL_CALL_COMPLETED))
    assert d_done.action == Action.DENY


# ---------------------------------------------------------------- Path aliases

def test_caller_alias_resolves_to_principal():
    rules = compile_rules("""
    RULE: r_caller
    ON:        tool_call(x)
    CONDITION: caller.role == "admin" AND caller.trust_level >= 2
    POLICY:    ALLOW
    """)
    r = rules[0]
    assert r.predicate(_ev("x", role="admin", trust=2), {})
    assert not r.predicate(_ev("x", role="basic", trust=2), {})


def test_principal_user_id_path_resolves():
    rules = compile_rules("""
    RULE: r_user
    ON:        tool_call(x)
    CONDITION: principal.user_id == "user-123"
    POLICY:    HUMAN_CHECK
    """)
    r = rules[0]
    ev = _ev("x")
    ev.principal.user_id = "user-123"
    assert r.predicate(ev, {})
    ev.principal.user_id = "user-456"
    assert not r.predicate(ev, {})


def test_tool_alias_and_tool_name():
    rules = compile_rules("""
    RULE: r_tool
    ON:        tool_call(*)
    CONDITION: tool.name == "http_post"
    POLICY:    DENY
    """)
    r = rules[0]
    assert r.predicate(_ev("http_post"), {})
    assert not r.predicate(_ev("db_query"), {})


def test_event_alias():
    rules = compile_rules("""
    RULE: r_event
    ON:        tool_call(*)
    CONDITION: event.session_id == "s-42"
    POLICY:    DENY
    """)
    r = rules[0]
    ev = _ev("x", session_id="s-42")
    assert r.predicate(ev, {})


# ---------------------------------------------------------------- Function predicates

def test_upstream_contains_tool():
    rules = compile_rules("""
    RULE: r_upstream
    ON:        tool_call(send_email)
    CONDITION: upstream_contains_tool("db_query")
    POLICY:    DENY
    """)
    r = rules[0]
    features = {"session.previous_tools": ["db_query", "format_report"]}
    assert r.predicate(_ev("send_email"), features)
    assert not r.predicate(_ev("send_email"), {"session.previous_tools": ["x"]})


def test_input_has_label_and_any():
    rules = compile_rules("""
    RULE: r_label
    ON:        tool_call(send_email)
    CONDITION: input.has_any_label({"finance/*", "hr/*"})
    POLICY:    DENY
    """)
    r = rules[0]
    assert r.predicate(_ev("send_email"), {"input.labels": ["finance/q1"]})
    assert r.predicate(_ev("send_email"), {"input.labels": ["hr/records"]})
    assert not r.predicate(_ev("send_email"), {"input.labels": ["public/news"]})


def test_caller_scope_missing():
    rules = compile_rules("""
    RULE: r_scope
    ON:        tool_call(send_email)
    CONDITION: caller.scope_missing("sensitive_export")
    POLICY:    DENY
    """)
    r = rules[0]
    assert r.predicate(_ev("send_email", scope=["read"]), {})
    assert not r.predicate(_ev("send_email", scope=["sensitive_export", "read"]), {})


def test_whitelist_function_as_value():
    rules = compile_rules("""
    RULE: r_wl
    ON:        tool_call(send_email)
    CONDITION: tool.target.domain NOT IN whitelist("approved_targets")
    POLICY:    DENY
    """)
    r = rules[0]
    feats = {"allowlist.approved_targets": {"internal.corp", "trusted.com"}}
    assert r.predicate(_ev("send_email", target={"domain": "evil.com"}), feats)
    assert not r.predicate(_ev("send_email", target={"domain": "internal.corp"}), feats)


def test_goal_drift_signal():
    rules = compile_rules("""
    RULE: r_drift
    ON:        tool_call(send_email)
    CONDITION: goal_drift_detected()
    POLICY:    DENY
    """)
    r = rules[0]
    assert not r.predicate(_ev("send_email"), {})
    assert r.predicate(_ev("send_email"), {"signal.goal_drift": True})


def test_repeated_attempts_numeric_compare():
    rules = compile_rules("""
    RULE: r_rep
    ON:        tool_call(send_email)
    CONDITION: repeated_attempts(tool="send_email", window="5m") > 2
    POLICY:    HUMAN_CHECK
    """)
    r = rules[0]
    feats = {"session.previous_tools": ["send_email", "send_email", "send_email"]}
    assert r.predicate(_ev("send_email"), feats)
    assert not r.predicate(_ev("send_email"), {"session.previous_tools": []})


# ---------------------------------------------------------------- exists_path

def test_exists_path_source_dot_label():
    rules = compile_rules("""
    RULE: r_ep
    ON:        tool_call(send_email)
    CONDITION: exists_path(source.label IN {"finance/*"}, sink = current_call)
    POLICY:    DENY
    """)
    r = rules[0]
    ev = _ev("send_email", extra={"session_labels": ["finance/q1"]})
    assert r.predicate(ev, {})


# ---------------------------------------------------------------- DEGRADE

def test_degrade_to_syntax():
    rules = compile_rules("""
    RULE: r_deg
    ON:        tool_call(send_email)
    CONDITION: caller.trust_level < 3
    POLICY:    DEGRADE TO "email.send_to_draft"
    """)
    r = rules[0]
    assert r.action == Action.DEGRADE
    assert r.degrade_profile == "email.send_to_draft"


# ---------------------------------------------------------------- Rule metadata

def test_rule_metadata():
    rules = compile_rules("""
    RULE: r_meta
    ON:        tool_call(send_email)
    CONDITION: tool.name == "send_email"
    POLICY:    DENY
    Severity:  high
    Category:  data_exfiltration
    Reason:    "Blocked external send"
    """)
    r = rules[0]
    assert r.severity == "high"
    assert r.category == "data_exfiltration"
    assert r.meta["reason"].startswith("Blocked")


# ---------------------------------------------------------------- Obligations

def test_action_with_redact():
    rules = compile_rules("""
    RULE: r_redact
    ON:        tool_call(send_email)
    CONDITION: tool.name == "send_email"
    POLICY:    ALLOW WITH REDACT(fields={"email", "phone"})
    """)
    r = rules[0]
    assert r.action == Action.ALLOW
    assert len(r.obligations_ast) == 1
    assert r.obligations_ast[0].kind == "REDACT"


def test_action_with_audit_and_redact_combo():
    rules = compile_rules("""
    RULE: r_combo
    ON:        tool_call(http_post)
    CONDITION: tool.target != "internal"
    POLICY:    DEGRADE TO "safe_http_post" WITH REDACT(fields={"ssn"}), AUDIT(severity="critical")
    """)
    r = rules[0]
    assert r.action == Action.DEGRADE
    assert r.degrade_profile == "safe_http_post"
    kinds = [o.kind for o in r.obligations_ast]
    assert "REDACT" in kinds and "AUDIT" in kinds


# ---------------------------------------------------------------- End-to-end (evaluator)

def test_e2e_build_obligation_redact_mutates_args():
    from agentguard.policy.evaluator.matcher import FastEvaluator
    from agentguard.degrade.transformers import ActionExecutor

    rules = compile_rules("""
    RULE: r_e2e
    ON:        tool_call(send_email)
    CONDITION: tool.name == "send_email"
    POLICY:    ALLOW WITH REDACT(fields={"email"})
    """)
    ev = FastEvaluator(rules)
    rt = _ev("send_email", args={"email": "me@x.com", "subject": "hi"})
    decision = ev.evaluate(rt)
    assert decision.action == Action.ALLOW
    kinds = {o.kind for o in decision.obligations}
    assert "mask_fields" in kinds
    rewritten = ActionExecutor().apply_rewrites(rt, decision)
    assert rewritten.args["email"] == "[REDACTED]"


def test_chain_defense_example():
    """sensitive_data_no_external_exfiltration — end-to-end."""
    from agentguard.policy.evaluator.matcher import FastEvaluator

    rules = compile_rules("""
    RULE: sensitive_data_no_external_exfiltration
    ON:        tool_call.requested
    CONDITION: tool.name IN {"send_email", "http_post", "slack_post"}
               AND exists_path(source.label IN {"finance/*", "hr/*"}, sink = current_call)
               AND (caller.scope_missing("sensitive_export") OR goal_drift_detected())
    POLICY:    DENY
    Severity:  high
    Category:  data_exfiltration
    Reason:    "Sensitive data flowing to an unapproved sink"
    """)
    ev = FastEvaluator(rules)

    rt_ok = _ev("send_email", scope=["sensitive_export"],
                extra={"session_labels": ["finance/q1"]})
    assert ev.evaluate(rt_ok).action == Action.ALLOW

    rt_missing = _ev("send_email", scope=[],
                     extra={"session_labels": ["finance/q1"]})
    d = ev.evaluate(rt_missing)
    assert d.action == Action.DENY
    assert d.matched_rules == ["sensitive_data_no_external_exfiltration"]
