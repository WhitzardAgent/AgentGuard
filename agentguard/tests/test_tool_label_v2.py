"""End-to-end tests for the v2 tool-label refactor.

Covers:
  * static labels (boundary / sensitivity / integrity / tags)
  * tool.<param> shorthand (syntax field access)
  * tool.result post-execution access
  * trace() DSL predicate over the chronological sequence
"""

from __future__ import annotations

import pytest

from agentguard import DecisionDenied, Guard, Principal


# ---------------------------------------------------------------------------
# Static labels — boundary / sensitivity / integrity
# ---------------------------------------------------------------------------

def test_boundary_external_blocks_high_sensitivity_call():
    guard = Guard(
        policy_source="""
        RULE: deny_external_high_sensitivity
        ON: tool_call.requested
        CONDITION: tool.boundary == "external" AND tool.sensitivity == "high"
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool(
        "send_email",
        sink_type="email",
        boundary="external",
        sensitivity="high",
    )
    def send_email(recipient: str, subject: str, body: str) -> str:
        return f"sent to {recipient}"

    p = Principal(agent_id="a", session_id="s1", role="default", trust_level=2)
    with guard.session(principal=p):
        with pytest.raises(DecisionDenied):
            send_email(recipient="x@y.com", subject="hi", body="hello")
    guard.close()


def test_internal_low_sensitivity_passes_through():
    guard = Guard(
        policy_source="""
        RULE: deny_external_high
        ON: tool_call.requested
        CONDITION: tool.boundary == "external" AND tool.sensitivity == "high"
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("internal_log", boundary="internal", sensitivity="low")
    def internal_log(message: str) -> str:
        return f"logged: {message}"

    p = Principal(agent_id="a", session_id="s2")
    with guard.session(principal=p):
        assert internal_log(message="hi") == "logged: hi"
    guard.close()


def test_integrity_unfiltered_triggers_human_check():
    from agentguard.degrade.planner import EnforcerConfig
    guard = Guard(
        policy_source="""
        RULE: review_unfiltered_integrity
        ON: tool_call.requested
        CONDITION: tool.integrity == "unfiltered" AND tool.boundary == "privileged"
        POLICY: HUMAN_CHECK
        """,
        builtin_rules=False,
        mode="enforce",
        enforcer_config=EnforcerConfig(
            approval_timeout_s=0.05, on_timeout="deny",
        ),
    )

    @guard.tool("shell_exec",
                boundary="privileged",
                sensitivity="high",
                integrity="unfiltered")
    def shell_exec(cmd: str) -> str:
        return f"ran {cmd}"

    p = Principal(agent_id="a", session_id="s3")
    from agentguard.models.errors import HumanApprovalPending
    with guard.session(principal=p):
        with pytest.raises((HumanApprovalPending, DecisionDenied)):
            shell_exec(cmd="ls")
    guard.close()


# ---------------------------------------------------------------------------
# tool.<param> shorthand path
# ---------------------------------------------------------------------------

def test_tool_param_shortcut_accesses_args():
    """``tool.recipient`` should resolve to ``tool_call.args["recipient"]``."""
    guard = Guard(
        policy_source="""
        RULE: deny_external_recipient
        ON: tool_call.requested
        CONDITION: tool.name == "send_email"
          AND tool.recipient == "attacker@evil.com"
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("send_email", boundary="external", sensitivity="moderate")
    def send_email(recipient: str, subject: str, body: str) -> str:
        return "sent"

    p = Principal(agent_id="a", session_id="s4")
    with guard.session(principal=p):
        with pytest.raises(DecisionDenied):
            send_email(recipient="attacker@evil.com", subject="x", body="y")
        # Other recipients pass
        assert send_email(recipient="ok@corp.com", subject="x", body="y") == "sent"
    guard.close()


def test_tool_param_shortcut_with_matches_operator():
    guard = Guard(
        policy_source="""
        RULE: deny_confidential_subject
        ON: tool_call.requested
        CONDITION: tool.name == "send_email"
          AND tool.subject MATCHES ".*[Cc]onfidential.*"
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("send_email", boundary="external")
    def send_email(recipient: str, subject: str, body: str) -> str:
        return "sent"

    p = Principal(agent_id="a", session_id="s5")
    with guard.session(principal=p):
        with pytest.raises(DecisionDenied):
            send_email(recipient="r@x.com", subject="Confidential Q1", body="b")
    guard.close()


# ---------------------------------------------------------------------------
# trace() DSL predicate
# ---------------------------------------------------------------------------

def test_trace_optional_gap_blocks_db_to_external_chain():
    """Classic exfiltration pattern: db.query somewhere upstream of http_post."""
    guard = Guard(
        policy_source="""
        RULE: deny_db_then_external
        ON: tool_call.requested
        CONDITION: tool.name == "http_post"
          AND trace("db_query -> ...? -> http_post")
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("db_query", boundary="internal", sensitivity="high")
    def db_query(sql: str) -> str:
        return "rows"

    @guard.tool("http_post", boundary="external", sensitivity="moderate")
    def http_post(url: str, data: dict) -> str:
        return "ok"

    p = Principal(agent_id="a", session_id="s6", role="default", trust_level=2)
    with guard.session(principal=p):
        # First call db_query → trace_log = ["db_query"]
        db_query(sql="SELECT * FROM customers")
        # Now http_post should fire the rule
        with pytest.raises(DecisionDenied) as exc:
            http_post(url="https://x.com", data={})
        assert "deny_db_then_external" in (exc.value.matched_rules or [])
    guard.close()


def test_trace_adjacent_only_fires_when_immediately_followed():
    guard = Guard(
        policy_source="""
        RULE: deny_a_immediately_b
        ON: tool_call.requested
        CONDITION: tool.name == "tool_b" AND trace("tool_a -> tool_b")
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("tool_a")
    def tool_a() -> str: return "a"

    @guard.tool("tool_b")
    def tool_b() -> str: return "b"

    @guard.tool("tool_c")
    def tool_c() -> str: return "c"

    p = Principal(agent_id="a", session_id="s7")
    with guard.session(principal=p):
        # adjacent → should fire
        tool_a()
        with pytest.raises(DecisionDenied):
            tool_b()
    guard.close()

    # different session: tool_a then tool_c then tool_b → NOT adjacent → allow
    guard2 = Guard(
        policy_source="""
        RULE: deny_a_immediately_b
        ON: tool_call.requested
        CONDITION: tool.name == "tool_b" AND trace("tool_a -> tool_b")
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard2.tool("tool_a")
    def tool_a2() -> str: return "a"

    @guard2.tool("tool_b")
    def tool_b2() -> str: return "b"

    @guard2.tool("tool_c")
    def tool_c2() -> str: return "c"

    p2 = Principal(agent_id="a", session_id="s7b")
    with guard2.session(principal=p2):
        tool_a2()
        tool_c2()
        # Now tool_b — sequence is [a, c, b]; "a -> b" adjacent does NOT match.
        assert tool_b2() == "b"
    guard2.close()


def test_trace_exactly_one_between():
    guard = Guard(
        policy_source="""
        RULE: deny_a_starone_b
        ON: tool_call.requested
        CONDITION: tool.name == "tool_b" AND trace("tool_a -> * -> tool_b")
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("tool_a")
    def tool_a() -> str: return "a"

    @guard.tool("tool_b")
    def tool_b() -> str: return "b"

    @guard.tool("tool_c")
    def tool_c() -> str: return "c"

    p = Principal(agent_id="a", session_id="s8")
    with guard.session(principal=p):
        tool_a()
        tool_c()
        # sequence at request time: [a, c] + b = [a, c, b] → matches "a -> * -> b"
        with pytest.raises(DecisionDenied):
            tool_b()
    guard.close()


def test_trace_non_empty_gap_does_not_fire_adjacent():
    guard = Guard(
        policy_source="""
        RULE: deny_a_dotdotdot_b
        ON: tool_call.requested
        CONDITION: tool.name == "tool_b" AND trace("tool_a -> ... -> tool_b")
        POLICY: DENY
        """,
        builtin_rules=False,
        mode="enforce",
    )

    @guard.tool("tool_a")
    def tool_a() -> str: return "a"

    @guard.tool("tool_b")
    def tool_b() -> str: return "b"

    p = Principal(agent_id="a", session_id="s9")
    with guard.session(principal=p):
        tool_a()
        # sequence [a, b] — adjacent → "..." (non-empty gap) does NOT match
        assert tool_b() == "b"
    guard.close()
