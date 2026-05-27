"""Tests for Enforcer ALLOW branch obligations and async wrap_tool support."""
from __future__ import annotations

import asyncio

import pytest

from agentguard.degrade.planner import _LLM_REVIEW_SYSTEM
from agentguard.sdk.guard import Guard
from agentguard.models.decisions import Action, Decision, Obligation
from agentguard.models.events import EventType
from agentguard.tests.conftest import build_event as _mk, make_principal, mini_guard


# ──────────────────────────────────────────────────────────────────────────────
# ALLOW + mask_fields obligation
# ──────────────────────────────────────────────────────────────────────────────

REDACT_DSL = """
RULE: allow_with_redact
ON: tool_call(http.post)
CONDITION: principal.role == "default"
POLICY: ALLOW WITH REDACT(fields={"email", "token"}), AUDIT(severity="low")
"""

LLM_TRACE_DSL = """
RULE: review_external_post
ON: tool_call(http.post)
CONDITION: args.url == "https://external.example/api"
POLICY: LLM_CHECK
"""

LLM_TRACE_V3_PROMPT_DSL = """
RULE: review-external-post
ON: tool_call(http.post)
CONDITION: args.url == "https://external.example/api"
POLICY: LLM_CHECK
Prompt: "Apply a strict outbound HTTP review policy. If destination trust is unclear, choose human."
Severity: high
Category: network
Reason: "Outbound HTTP request requires careful review."
"""

LLM_TRACE_V3_EMPTY_PROMPT_DSL = """
RULE: review-external-post
ON: tool_call(http.post)
CONDITION: args.url == "https://external.example/api"
POLICY: LLM_CHECK
Prompt: ""
Severity: high
Category: network
"""


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _CaptureLLMBackend:
    def __init__(self, verdict: str = "allow"):
        self.verdict = verdict
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.messages.append(messages)
        return _FakeLLMResponse(self.verdict)


class _StaticContentLLMBackend:
    def __init__(self, content: str):
        self.content = content
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.messages.append(messages)
        return _FakeLLMResponse(self.content)


def test_allow_branch_applies_redact_obligation():
    """ALLOW rules with REDACT must redact the specified fields before calling the tool."""
    guard = mini_guard(REDACT_DSL)
    results = []

    def executor(event):
        results.append(dict(event.tool_call.args))
        return "ok"

    ev = _mk(
        "http.post",
        args={"url": "https://example.com", "email": "user@x.com", "token": "s3cr3t"},
    )
    guard.pipeline.guarded_call(ev, executor)

    assert len(results) == 1
    assert results[0].get("email") == "[REDACTED]"
    assert results[0].get("token") == "[REDACTED]"
    assert results[0].get("url") == "https://example.com"


def test_local_llm_check_prompt_includes_trace_summary():
    backend = _CaptureLLMBackend("allow")
    guard = Guard(policy_source=LLM_TRACE_DSL, builtin_rules=False, llm_backend=backend)
    principal = make_principal(session_id="trace-llm-local")

    first = _mk(
        "fs.read",
        args={"path": "/tmp/report.txt"},
        principal=principal,
        sink_type="none",
    )
    second = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        principal=principal,
        sink_type="http",
    )

    guard.pipeline.guarded_call(first, lambda _event: "report-body")
    guard.pipeline.guarded_call(second, lambda _event: "sent")

    assert backend.messages
    user_prompt = backend.messages[-1][1]["content"]
    assert "Trace summary:" in user_prompt
    assert 'fs.read(path="/tmp/report.txt", result="report-body")' in user_prompt
    assert 'http.post(url="https://external.example/api"' not in user_prompt
    guard.close()


def test_local_llm_check_trace_summary_respects_env_max_steps(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_LLM_TRACE_MAX_STEPS", "1")
    backend = _CaptureLLMBackend("allow")
    guard = Guard(policy_source=LLM_TRACE_DSL, builtin_rules=False, llm_backend=backend)
    principal = make_principal(session_id="trace-llm-local-max-steps")

    first = _mk(
        "fs.read",
        args={"path": "/tmp/a.txt"},
        principal=principal,
        sink_type="none",
    )
    second = _mk(
        "db.query",
        args={"sql": "select 1"},
        principal=principal,
        sink_type="none",
    )
    trigger = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        principal=principal,
        sink_type="http",
    )

    guard.pipeline.guarded_call(first, lambda _event: "a")
    guard.pipeline.guarded_call(second, lambda _event: "b")
    guard.pipeline.guarded_call(trigger, lambda _event: "sent")

    assert backend.messages
    user_prompt = backend.messages[-1][1]["content"]
    assert "Trace summary:" in user_prompt
    assert 'db.query(sql="select 1", result="b")' in user_prompt
    assert 'fs.read(path="/tmp/a.txt", result="a")' not in user_prompt
    guard.close()


def test_llm_check_uses_custom_v3_prompt_as_system_prompt():
    backend = _StaticContentLLMBackend(
        "<DECISION>allow</DECISION><REASON>Destination is internal and request is scoped.</REASON>"
    )
    guard = Guard(policy_source=LLM_TRACE_V3_PROMPT_DSL, builtin_rules=False, llm_backend=backend)
    principal = make_principal(session_id="trace-llm-v3-prompt")

    trigger = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        principal=principal,
        sink_type="http",
    )

    guard.pipeline.guarded_call(trigger, lambda _event: "sent")

    assert backend.messages
    system_prompt = backend.messages[-1][0]["content"]
    assert system_prompt.startswith(
        "Apply a strict outbound HTTP review policy. If destination trust is unclear, choose human."
    )
    assert _LLM_REVIEW_SYSTEM in system_prompt
    assert "<DECISION>" in system_prompt
    assert "<REASON>" in system_prompt
    guard.close()


def test_llm_check_falls_back_to_default_system_prompt_when_v3_prompt_empty():
    backend = _StaticContentLLMBackend(
        "<DECISION>allow</DECISION><REASON>Request is low risk.</REASON>"
    )
    guard = Guard(policy_source=LLM_TRACE_V3_EMPTY_PROMPT_DSL, builtin_rules=False, llm_backend=backend)
    principal = make_principal(session_id="trace-llm-v3-empty-prompt")

    trigger = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        principal=principal,
        sink_type="http",
    )

    guard.pipeline.guarded_call(trigger, lambda _event: "sent")

    assert backend.messages
    system_prompt = backend.messages[-1][0]["content"]
    assert system_prompt == _LLM_REVIEW_SYSTEM
    guard.close()


def test_llm_check_reason_includes_rule_reason_and_llm_reason():
    backend = _StaticContentLLMBackend(
        "<DECISION>deny</DECISION><REASON>External destination lacks a verified business need.</REASON>"
    )
    guard = Guard(policy_source=LLM_TRACE_V3_PROMPT_DSL, builtin_rules=False, llm_backend=backend)
    principal = make_principal(session_id="trace-llm-v3-reason")

    trigger = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        principal=principal,
        sink_type="http",
    )

    with pytest.raises(Exception) as exc:
        guard.pipeline.guarded_call(trigger, lambda _event: "sent")

    reason = str(exc.value)
    assert "llm_denied:" in reason
    assert "rule_reason=Outbound HTTP request requires careful review." in reason
    assert "llm_reason=External destination lacks a verified business need." in reason
    guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# ALLOW + require_target_in obligation
# ──────────────────────────────────────────────────────────────────────────────

REQUIRE_TARGET_DSL = """
RULE: allow_with_require_target
ON: tool_call(http.post)
CONDITION: principal.role == "default"
POLICY: ALLOW WITH REQUIRE_TARGET_IN(whitelist={"safe.com", "trusted.org"})
"""


def test_allow_require_target_in_blocks_bad_domain():
    from agentguard.degrade.planner import DecisionDenied
    guard = mini_guard(REQUIRE_TARGET_DSL)
    ev = _mk(
        "http.post",
        args={"url": "https://evil.com"},
        target={"domain": "evil.com"},
    )
    with pytest.raises((DecisionDenied, Exception), match="require_target_in|evil.com"):
        guard.pipeline.guarded_call(ev, lambda e: "ok")


def test_allow_require_target_in_passes_good_domain():
    guard = mini_guard(REQUIRE_TARGET_DSL)
    ev = _mk(
        "http.post",
        args={"url": "https://safe.com"},
        target={"domain": "safe.com"},
    )
    result = guard.pipeline.guarded_call(ev, lambda e: "allowed_result")
    assert result == "allowed_result"


# ──────────────────────────────────────────────────────────────────────────────
# rate_limit counter
# ──────────────────────────────────────────────────────────────────────────────

def test_rate_limit_counts_calls():
    """rate_limit obligation should count calls in the sliding window."""
    from agentguard.degrade.transformers import ActionExecutor, _RATE_COUNTERS, _RATE_LOCK
    from agentguard.models.decisions import Obligation

    # Clear any stale state
    with _RATE_LOCK:
        _RATE_COUNTERS.clear()

    executor = ActionExecutor()
    ob = Obligation(kind="rate_limit", params={"rule_id": "test_rl", "max": 2, "window": "60s"})
    decision = Decision(action=Action.ALLOW, reason="ok", risk_score=0.0, obligations=[ob])
    ev = _mk("tool_x")

    assert executor.check_rate_limit(ev, decision) is None  # 1st call: ok
    assert executor.check_rate_limit(ev, decision) is None  # 2nd call: ok
    violation = executor.check_rate_limit(ev, decision)     # 3rd call: over limit
    assert violation is not None
    assert "rate limit exceeded" in violation


# ──────────────────────────────────────────────────────────────────────────────
# async wrap_tool
# ──────────────────────────────────────────────────────────────────────────────

def test_wrap_tool_sync_works():
    """Sync wrap_tool still works as before."""
    guard = mini_guard()

    def my_tool(x: int, y: int) -> int:
        return x + y

    wrapped = guard.tool("add")(my_tool)
    assert wrapped.__wrapped__ is my_tool
    result = wrapped(2, 3)
    assert result == 5


@pytest.mark.asyncio
async def test_wrap_tool_async_works():
    """Async wrap_tool preserves async behaviour."""
    guard = mini_guard()
    call_log = []

    async def my_async_tool(value: str) -> str:
        call_log.append(value)
        return f"async_{value}"

    wrapped = guard.tool("async_op")(my_async_tool)
    assert asyncio.iscoroutinefunction(wrapped)
    assert wrapped.__wrapped__ is my_async_tool

    result = await wrapped("hello")
    assert result == "async_hello"
    assert call_log == ["hello"]


@pytest.mark.asyncio
async def test_wrap_tool_async_deny_raises():
    """DENY decision on an async tool must raise DecisionDenied (or equivalent)."""
    from agentguard.degrade.planner import DecisionDenied

    DENY_DSL = """
RULE: deny_async
ON: tool_call(async_op)
CONDITION: principal.role == "default"
POLICY: DENY
"""
    guard = mini_guard(DENY_DSL)

    async def my_async_tool(value: str) -> str:
        return f"async_{value}"

    wrapped = guard.tool("async_op")(my_async_tool)
    with pytest.raises((DecisionDenied, Exception)):
        await wrapped("should_be_blocked")
