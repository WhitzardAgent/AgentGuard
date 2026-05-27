"""Tests for the parameter-level DSL extensions added in Round 1:

- ``MATCHES`` operator (regex)
- ``CONTAINS`` operator (polymorphic membership / substring)
- ``starts_with`` / ``ends_with`` / ``contains`` functions
- ``url.domain`` / ``url.is_external`` / ``email.domain`` helpers

These primitives let rule authors move from tool-name-level matching
("any send_email") to parameter-level matching ("send_email whose first
recipient ends with @evil.com").
"""

from __future__ import annotations

import pytest

from agentguard.models.events import (
    EventType, Principal, RuntimeEvent, ToolCall,
)
from agentguard.policy.dsl.compiler import compile_rules


def _ev(
    tool: str = "send_email",
    args: dict | None = None,
    sink_type: str = "email",
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_REQUESTED,
        principal=Principal(
            agent_id="a", session_id="s-test", role="planner", trust_level=1,
        ),
        tool_call=ToolCall(
            tool_name=tool,
            args=args or {},
            target={},
            sink_type=sink_type,
        ),
        scope=[],
        extra={},
    )


# =================================================================
# MATCHES (regex)
# =================================================================

def test_matches_basic_anchor():
    rules = compile_rules(r"""
    RULE: r1
    ON: tool_call(send_email)
    CONDITION: args.url MATCHES "^https://internal\."
    POLICY: DENY
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"url": "https://internal.example.com/x"}), {}) is True
    assert r.predicate(_ev(args={"url": "https://external.com/x"}), {}) is False


def test_matches_iban_format():
    rules = compile_rules(r"""
    RULE: r_iban
    ON: tool_call(send_money)
    CONDITION: args.recipient MATCHES "^DE\d{20}$"
    POLICY: ALLOW
    """)
    r = rules[0]
    assert r.predicate(_ev("send_money", args={"recipient": "DE89370400440532013000"}), {}) is True
    assert r.predicate(_ev("send_money", args={"recipient": "FR1420041010050500013M02606"}), {}) is False
    assert r.predicate(_ev("send_money", args={"recipient": "DE89"}), {}) is False


def test_matches_against_missing_field_is_false():
    rules = compile_rules(r"""
    RULE: r_missing
    ON: tool_call(send_email)
    CONDITION: args.url MATCHES "^https://"
    POLICY: DENY
    """)
    r = rules[0]
    assert r.predicate(_ev(args={}), {}) is False


def test_matches_with_invalid_regex_returns_false():
    """A bad pattern must not raise; rule should evaluate to False."""
    rules = compile_rules(r"""
    RULE: r_bad
    ON: tool_call(send_email)
    CONDITION: args.url MATCHES "(unbalanced"
    POLICY: DENY
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"url": "anything"}), {}) is False


# =================================================================
# CONTAINS (polymorphic)
# =================================================================

def test_contains_list_membership():
    rules = compile_rules("""
    RULE: r_list
    ON: tool_call(send_email)
    CONDITION: args.recipients CONTAINS "attacker@evil.com"
    POLICY: DENY
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"recipients": ["a@x.com", "attacker@evil.com"]}), {}) is True
    assert r.predicate(_ev(args={"recipients": ["a@x.com", "b@y.com"]}), {}) is False


def test_contains_substring_in_string():
    rules = compile_rules("""
    RULE: r_str
    ON: tool_call(send_email)
    CONDITION: args.body CONTAINS "click here to verify"
    POLICY: HUMAN_CHECK
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"body": "Please click here to verify your account"}), {}) is True
    assert r.predicate(_ev(args={"body": "Hello"}), {}) is False


def test_contains_dict_key():
    rules = compile_rules("""
    RULE: r_dict
    ON: tool_call(call_api)
    CONDITION: args.headers CONTAINS "Authorization"
    POLICY: ALLOW
    """)
    r = rules[0]
    assert r.predicate(_ev("call_api", args={"headers": {"Authorization": "x"}}), {}) is True
    assert r.predicate(_ev("call_api", args={"headers": {"Cookie": "x"}}), {}) is False


def test_contains_none_returns_false():
    rules = compile_rules("""
    RULE: r_none
    ON: tool_call(send_email)
    CONDITION: args.recipients CONTAINS "x"
    POLICY: DENY
    """)
    r = rules[0]
    assert r.predicate(_ev(args={}), {}) is False


# =================================================================
# starts_with / ends_with / contains (functions)
# =================================================================

def test_starts_with_function():
    rules = compile_rules("""
    RULE: r_sw
    ON: tool_call(send_email)
    CONDITION: starts_with(args.url, "https://internal.")
    POLICY: ALLOW
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"url": "https://internal.foo/x"}), {}) is True
    assert r.predicate(_ev(args={"url": "https://external.com"}), {}) is False
    assert r.predicate(_ev(args={"url": None}), {}) is False
    assert r.predicate(_ev(args={}), {}) is False


def test_ends_with_function():
    rules = compile_rules("""
    RULE: r_ew
    ON: tool_call(send_email)
    CONDITION: ends_with(args.recipient, "@trusted.com")
    POLICY: ALLOW
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"recipient": "alice@trusted.com"}), {}) is True
    assert r.predicate(_ev(args={"recipient": "alice@evil.com"}), {}) is False


def test_contains_function_substring():
    rules = compile_rules("""
    RULE: r_cf
    ON: tool_call(send_email)
    CONDITION: contains(args.body, "click")
    POLICY: HUMAN_CHECK
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"body": "please click here"}), {}) is True
    assert r.predicate(_ev(args={"body": "hi"}), {}) is False


# =================================================================
# url.domain / url.is_external
# =================================================================

def test_url_domain_extraction():
    rules = compile_rules("""
    RULE: r_ud
    ON: tool_call(get_webpage)
    CONDITION: url.domain(args.url) == "example.com"
    POLICY: ALLOW
    """)
    r = rules[0]
    assert r.predicate(_ev("get_webpage", args={"url": "https://example.com/path?q=1"}), {}) is True
    # subdomains do NOT equal example.com — strict match
    assert r.predicate(_ev("get_webpage", args={"url": "https://api.example.com/"}), {}) is False
    assert r.predicate(_ev("get_webpage", args={"url": "https://other.com/"}), {}) is False


def test_url_is_external_with_internal_allowlist():
    rules = compile_rules("""
    RULE: r_ext
    ON: tool_call(get_webpage)
    CONDITION: url.is_external(args.url)
    POLICY: HUMAN_CHECK
    """)
    r = rules[0]
    feats = {"allowlist.internal_domains": ["internal.example.com", "corp.local"]}
    # exact match → internal
    assert r.predicate(_ev("get_webpage", args={"url": "https://internal.example.com/x"}), feats) is False
    # subdomain match (a.internal.example.com ends-with .internal.example.com) → internal
    assert r.predicate(_ev("get_webpage", args={"url": "https://a.internal.example.com/x"}), feats) is False
    # outside the allowlist → external
    assert r.predicate(_ev("get_webpage", args={"url": "https://evil.com/x"}), feats) is True


def test_url_is_external_with_no_allowlist_means_all_external():
    rules = compile_rules("""
    RULE: r_ext_default
    ON: tool_call(get_webpage)
    CONDITION: url.is_external(args.url)
    POLICY: HUMAN_CHECK
    """)
    r = rules[0]
    assert r.predicate(_ev("get_webpage", args={"url": "https://anything.com/"}), {}) is True


# =================================================================
# email.domain
# =================================================================

def test_email_domain_in_set():
    rules = compile_rules("""
    RULE: r_ed
    ON: tool_call(send_email)
    CONDITION: email.domain(args.recipient) IN {"evil.com", "attacker.com"}
    POLICY: DENY
    """)
    r = rules[0]
    assert r.predicate(_ev(args={"recipient": "bob@evil.com"}), {}) is True
    assert r.predicate(_ev(args={"recipient": "bob@trusted.com"}), {}) is False
    assert r.predicate(_ev(args={"recipient": "not-an-email"}), {}) is False


# =================================================================
# Combined: MATCHES + CONTAINS + functions in one rule
# =================================================================

# =================================================================
# whitelist() reading from ev.extra["allowlists"] (session-scoped)
# =================================================================

def test_whitelist_from_session_extra():
    """whitelist() falls back to ev.extra['allowlists'] when feature
    is absent — this is how AgentGuard-AgentDojo plumbs per-session
    user-trusted entities."""
    rules = compile_rules("""
    RULE: r_session_wl
    ON: tool_call(send_money)
    CONDITION: args.recipient IN whitelist("user_known_ibans")
    POLICY: ALLOW
    """)
    r = rules[0]
    ev = _ev(
        "send_money",
        args={"recipient": "GB29NWBK60161331926819"},
    )
    ev.extra["allowlists"] = {
        "user_known_ibans": ["GB29NWBK60161331926819", "DE89370400440532013000"],
    }
    assert r.predicate(ev, {}) is True

    # Recipient not in the list → no match
    ev2 = _ev("send_money", args={"recipient": "ATTACKER000000000000"})
    ev2.extra["allowlists"] = {"user_known_ibans": ["GB29NWBK60161331926819"]}
    assert r.predicate(ev2, {}) is False

    # Empty session allowlists dict → no match
    ev3 = _ev("send_money", args={"recipient": "GB29NWBK60161331926819"})
    ev3.extra["allowlists"] = {}
    assert r.predicate(ev3, {}) is False


# =================================================================
# subset() / any_in() — list quantifiers
# =================================================================

def test_subset_all_recipients_in_whitelist():
    """All recipients are in the address book → ALLOW."""
    rules = compile_rules("""
    RULE: r_subset
    ON: tool_call(send_email)
    CONDITION: subset(args.recipients, whitelist("user_address_book"))
    POLICY: ALLOW
    """)
    r = rules[0]
    ev = _ev(args={"recipients": ["alice@x.com", "bob@x.com"]})
    ev.extra["allowlists"] = {
        "user_address_book": ["alice@x.com", "bob@x.com", "carol@x.com"],
    }
    assert r.predicate(ev, {}) is True

    # Missing one recipient → False
    ev2 = _ev(args={"recipients": ["alice@x.com", "stranger@evil.com"]})
    ev2.extra["allowlists"] = {"user_address_book": ["alice@x.com", "bob@x.com"]}
    assert r.predicate(ev2, {}) is False


def test_any_in_blocklist_match():
    """Any recipient on the blocklist triggers DENY."""
    rules = compile_rules("""
    RULE: r_any_in
    ON: tool_call(send_email)
    CONDITION: any_in(args.recipients, whitelist("blocked_emails"))
    POLICY: DENY
    """)
    r = rules[0]
    ev = _ev(args={"recipients": ["alice@x.com", "attacker@evil.com"]})
    ev.extra["allowlists"] = {"blocked_emails": ["attacker@evil.com"]}
    assert r.predicate(ev, {}) is True

    ev2 = _ev(args={"recipients": ["alice@x.com", "bob@x.com"]})
    ev2.extra["allowlists"] = {"blocked_emails": ["attacker@evil.com"]}
    assert r.predicate(ev2, {}) is False


def test_whitelist_features_take_precedence_over_session():
    """If the same key appears in both features and ev.extra, features wins
    (legacy global allowlists override session-scoped ones)."""
    rules = compile_rules("""
    RULE: r_pri
    ON: tool_call(send_money)
    CONDITION: args.recipient IN whitelist("user_known_ibans")
    POLICY: ALLOW
    """)
    r = rules[0]
    ev = _ev("send_money", args={"recipient": "FROM_FEATURES"})
    ev.extra["allowlists"] = {"user_known_ibans": ["FROM_SESSION_ONLY"]}
    feats = {"allowlist.user_known_ibans": ["FROM_FEATURES"]}
    assert r.predicate(ev, feats) is True


def test_combined_param_and_chain_rule():
    rules = compile_rules(r"""
    RULE: r_combined
    ON: tool_call(send_email)
    CONDITION: args.recipients CONTAINS "attacker@evil.com"
       OR ends_with(args.subject, "[urgent]")
       OR args.body MATCHES "(?i)click\s+here"
    POLICY: DENY
    """)
    r = rules[0]
    # match #1: recipient list contains attacker
    ev1 = _ev(args={"recipients": ["a@x.com", "attacker@evil.com"], "subject": "Hi", "body": "ok"})
    assert r.predicate(ev1, {}) is True
    # match #2: subject ends with [urgent]
    ev2 = _ev(args={"recipients": ["a@x.com"], "subject": "FYI [urgent]", "body": "ok"})
    assert r.predicate(ev2, {}) is True
    # match #3: regex case-insensitive
    ev3 = _ev(args={"recipients": ["a@x.com"], "subject": "Hi", "body": "Please CLICK HERE now"})
    assert r.predicate(ev3, {}) is True
    # no match
    ev4 = _ev(args={"recipients": ["a@x.com"], "subject": "Hi", "body": "thanks"})
    assert r.predicate(ev4, {}) is False
