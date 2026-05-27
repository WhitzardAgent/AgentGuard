"""Tests for single-tool DSL compatibility (v3 only).

Covers:
  - v3 unconditional rules (no CONDITION)
  - v3 TRACE clause with a single placeholder step
  - trace() predicate with a single tool name
  - validator output for these forms
"""

from __future__ import annotations

import pytest

from agentguard.models.decisions import Action
from agentguard.models.events import (
    EventType, Principal, RuntimeEvent, ToolCall,
)
from agentguard.policy.dsl.compiler import compile_rules
from agentguard.policy.dsl.parser import parse_rule_source
from agentguard.policy.dsl.validator import validate_source


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ev(tool: str = "shell.exec", role: str = "planner",
        session_id: str = "s1") -> RuntimeEvent:
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_REQUESTED,
        principal=Principal(agent_id="a", session_id=session_id,
                            role=role, trust_level=1),
        tool_call=ToolCall(tool_name=tool, args={}, target={}, sink_type="none"),
        scope=[],
        extra={},
    )


def _feats(trace_rich: list[dict] | None = None) -> dict:
    return {"session.trace_rich": trace_rich or []}


# ──────────────────────────────────────────────────────────────────────────────
# v3: single-step TRACE clause
# ──────────────────────────────────────────────────────────────────────────────

class TestV3SingleStepTrace:
    def test_single_step_trace_parses(self):
        asts = parse_rule_source("""
        RULE: single-trace-rule
        ON:        tool_call.requested
        TRACE:     T
        CONDITION: T.name == "python.eval"
        POLICY:    DENY
        """)
        tc = asts[0].trace_clause
        assert tc is not None
        assert len(tc.steps) == 1
        assert tc.steps[0].name == "T"

    def test_single_step_trace_compiles(self):
        rules = compile_rules("""
        RULE: deny-eval
        ON:        tool_call.requested
        TRACE:     T
        CONDITION: T.name == "python.eval"
        POLICY:    DENY
        """)
        assert len(rules) == 1
        assert rules[0].action == Action.DENY

    def test_single_step_trace_fires_on_match(self):
        rules = compile_rules("""
        RULE: deny-eval
        TRACE:     T
        CONDITION: T.name == "python.eval"
        POLICY:    DENY
        """)
        ev = _ev("python.eval")
        # current call is appended inside _wrap_trace_predicate, so T binds to it
        assert rules[0].predicate(ev, _feats([]))

    def test_single_step_trace_does_not_fire_on_mismatch(self):
        rules = compile_rules("""
        RULE: deny-eval
        TRACE:     T
        CONDITION: T.name == "python.eval"
        POLICY:    DENY
        """)
        ev = _ev("fs.read")
        assert not rules[0].predicate(ev, _feats([]))

    def test_single_step_binds_to_current_call(self):
        """With prior history, T must bind to the CURRENT call, not an earlier one."""
        rules = compile_rules("""
        RULE: block-specific
        TRACE:     T
        CONDITION: T.name == "dangerous_tool"
        POLICY:    DENY
        """)
        prior = [{"tool": "safe_tool", "args": {}, "result": None, "ts_ms": 1}]
        ev = _ev("dangerous_tool")
        assert rules[0].predicate(ev, _feats(prior))

    def test_single_step_no_condition_fires_always(self):
        """Single-step TRACE without CONDITION fires for every call."""
        rules = compile_rules("""
        RULE: trace-any
        TRACE:  T
        POLICY: DENY
        """)
        ev = _ev("any_tool")
        assert rules[0].predicate(ev, _feats([]))

    def test_single_step_validator_no_errors(self):
        src = """
        RULE: deny-eval
        TRACE:     T
        CONDITION: T.name == "python.eval"
        POLICY:    DENY
        """
        report = validate_source(src)
        errors = [d for d in report.diagnostics if d.level == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_single_step_without_condition_emits_hint(self):
        """Missing CONDITION on a single-step TRACE should produce a hint (not error)."""
        src = """
        RULE: trace-any
        TRACE:  T
        POLICY: DENY
        """
        report = validate_source(src)
        errors = [d for d in report.diagnostics if d.level == "error"]
        hints = [d for d in report.diagnostics if d.level == "hint"]
        assert errors == []
        assert any("TRACE clause present" in h.message for h in hints)

    def test_single_step_hint_uses_placeholder_name(self):
        """The hint suggestion should reference the actual placeholder name."""
        src = """
        RULE: trace-any
        TRACE:  MyTool
        POLICY: DENY
        """
        report = validate_source(src)
        hints = [d for d in report.diagnostics if d.level == "hint"]
        trace_hints = [h for h in hints if "TRACE clause present" in h.message]
        assert trace_hints, "expected a TRACE hint"
        assert "MyTool" in trace_hints[0].suggestion


# ──────────────────────────────────────────────────────────────────────────────
# trace() function predicate with single tool
# ──────────────────────────────────────────────────────────────────────────────

class TestTraceFunctionSingleTool:
    def test_single_tool_trace_function_validates(self):
        """trace('shell.exec') should pass validation without errors."""
        src = """
        RULE: r
        ON:        tool_call(*)
        CONDITION: trace("shell.exec")
        POLICY:    DENY
        """
        report = validate_source(src)
        errors = [d for d in report.diagnostics if d.level == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_single_tool_trace_function_compiles(self):
        rules = compile_rules("""
        RULE: r
        ON:        tool_call(*)
        CONDITION: trace("shell.exec")
        POLICY:    DENY
        """)
        assert len(rules) == 1
        assert rules[0].action == Action.DENY


# ──────────────────────────────────────────────────────────────────────────────
# v3 unconditional rules (no CONDITION)
# ──────────────────────────────────────────────────────────────────────────────

class TestV3Unconditional:
    def test_bare_deny_compiles(self):
        rules = compile_rules("""
        RULE: deny-shell
        ON:     tool_call(shell.exec)
        POLICY: DENY
        """)
        assert len(rules) == 1
        assert rules[0].action == Action.DENY

    def test_bare_deny_fires(self):
        rules = compile_rules("""
        RULE: deny-exec
        ON:     tool_call(shell.exec)
        POLICY: DENY
        """)
        assert rules[0].predicate(_ev("shell.exec"), _feats())

    def test_wildcard_pattern(self):
        rules = compile_rules("""
        RULE: deny-all
        ON:     tool_call(*)
        POLICY: DENY
        """)
        assert rules[0].action == Action.DENY
        assert rules[0].tool_pattern == "*"

    def test_unconditional_with_subtype(self):
        rules = compile_rules("""
        RULE: deny-requested
        ON:     tool_call.requested(shell.exec)
        POLICY: DENY
        """)
        assert rules[0].event_subtype == "requested"
        assert rules[0].action == Action.DENY

    def test_unconditional_with_metadata(self):
        rules = compile_rules("""
        RULE: deny-exec
        ON:       tool_call(shell.exec)
        POLICY:   DENY
        Severity: critical
        Category: runtime
        """)
        r = rules[0]
        assert r.action == Action.DENY
        assert r.severity == "critical"

    def test_validator_accepts_unconditional_rule(self):
        src = """
        RULE: bare
        ON:     tool_call(x)
        POLICY: DENY
        """
        report = validate_source(src)
        errors = [d for d in report.diagnostics if d.level == "error"]
        assert errors == [], f"Unexpected errors: {errors}"
