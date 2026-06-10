"""Tests for Enforcer ALLOW branch obligations and async wrap_tool support."""
from __future__ import annotations

import asyncio
import json

import pytest

from agentguard.llm.security_review import (
    SECURITY_REVIEW_SYSTEM,
    parse_security_review_response,
)
from agentguard.sdk.guard import Guard
from agentguard.models.decisions import Action, Decision, Obligation
from agentguard.models.events import EventType
from agentguard.models.security_review import SecurityReviewResult, ThreatFinding, ThreatSeverity, ThreatType
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


def _review_json(
    severity: str = "none",
    *,
    threat_type: str = "trace_anomaly",
    summary: str = "No material threat detected.",
    evidence: list[str] | None = None,
    reason: str = "No unsafe pattern found.",
) -> str:
    findings = []
    if severity != "none":
        findings.append({
            "threat_type": threat_type,
            "severity": severity,
            "confidence": 0.91,
            "evidence": evidence or ["test evidence"],
            "reason": reason,
        })
    return json.dumps({
        "overall_severity": severity,
        "findings": findings,
        "summary": summary,
    })


class _CaptureLLMBackend:
    def __init__(self, content: str | None = None):
        self.content = content or _review_json("none")
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.messages.append(messages)
        return _FakeLLMResponse(self.content)


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


def test_security_review_json_parser_accepts_structured_findings():
    result = parse_security_review_response(_review_json(
        "critical",
        threat_type="prompt_injection",
        summary="External content attempted to override policy.",
        evidence=["ignore previous instructions"],
    ))
    assert result.max_severity().value == "critical"
    assert result.threat_types() == ["prompt_injection"]
    assert "ignore previous instructions" in result.evidence_preview()[0]


def test_security_review_json_parser_rejects_malformed_output():
    with pytest.raises(ValueError):
        parse_security_review_response("<DECISION>allow</DECISION>")


def test_decision_security_review_serializes_and_old_payloads_still_validate():
    review = SecurityReviewResult(
        overall_severity=ThreatSeverity.CRITICAL,
        findings=[
            ThreatFinding(
                threat_type=ThreatType.PROMPT_INJECTION,
                severity=ThreatSeverity.CRITICAL,
                confidence=0.9,
                evidence=["ignore previous instructions"],
            )
        ],
        summary="Prompt injection detected.",
    )
    decision = Decision(action=Action.DENY, security_review=review)

    payload = decision.model_dump(mode="json")
    assert payload["security_review"]["overall_severity"] == "critical"
    assert payload["security_review"]["findings"][0]["threat_type"] == "prompt_injection"
    assert Decision.model_validate({"action": "allow"}).security_review is None


def test_local_llm_check_prompt_includes_trace_summary():
    backend = _CaptureLLMBackend()
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
    assert '"trace_rich"' in user_prompt
    assert "/tmp/report.txt" in user_prompt
    assert "report-body" in user_prompt
    assert "https://external.example/api" in user_prompt
    guard.close()


def test_local_llm_check_trace_summary_respects_env_max_steps(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_LLM_TRACE_MAX_STEPS", "1")
    backend = _CaptureLLMBackend()
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
    assert '"trace_rich"' in user_prompt
    assert "select 1" in user_prompt
    assert '"result": "b"' in user_prompt
    assert "/tmp/a.txt" not in user_prompt
    guard.close()


def test_llm_check_uses_custom_v3_prompt_as_system_prompt():
    backend = _StaticContentLLMBackend(
        _review_json("none", summary="Request is low risk.")
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
    assert SECURITY_REVIEW_SYSTEM in system_prompt
    assert "strict JSON" in system_prompt
    assert "prompt_injection" in system_prompt
    guard.close()


def test_llm_check_falls_back_to_default_system_prompt_when_v3_prompt_empty():
    backend = _StaticContentLLMBackend(
        _review_json("none", summary="Request is low risk.")
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
    assert system_prompt.startswith(SECURITY_REVIEW_SYSTEM)
    assert "Active prompt packs:" in system_prompt
    assert "Prompt pack: prompt_injection" in system_prompt
    guard.close()


def test_llm_check_critical_review_denies_and_includes_findings_in_reason():
    backend = _StaticContentLLMBackend(
        _review_json(
            "critical",
            threat_type="prompt_injection",
            summary="External destination lacks a verified business need.",
            evidence=["untrusted payload asks to exfiltrate secrets"],
            reason="Potential exfiltration.",
        )
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
    assert "security_review:critical:" in reason
    assert "threats=prompt_injection" in reason
    assert "untrusted payload asks to exfiltrate secrets" in reason
    assert "rule_reason=Outbound HTTP request requires careful review." in reason
    guard.close()


def test_llm_check_high_review_maps_to_human_check():
    backend = _StaticContentLLMBackend(
        _review_json(
            "high",
            threat_type="trace_anomaly",
            summary="Sensitive read followed by outbound post.",
        )
    )
    guard = Guard(policy_source=LLM_TRACE_DSL, builtin_rules=False, llm_backend=backend)
    principal = make_principal(session_id="trace-llm-high")
    trigger = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        principal=principal,
        sink_type="http",
    )

    initial = guard.pipeline.handle_attempt(trigger)
    resolved = guard.pipeline.enforcer.resolve_remote_decision(trigger, initial)

    assert resolved.action is Action.HUMAN_CHECK
    assert resolved.client_action is not None
    assert resolved.client_action.value == "human_check"
    assert "security_review:high:" in resolved.reason
    assert resolved.security_review is not None
    assert resolved.security_review.overall_severity is ThreatSeverity.HIGH
    assert resolved.security_review.findings[0].threat_type is ThreatType.TRACE_ANOMALY
    guard.close()


def test_llm_check_malformed_review_escalates_to_human_check():
    backend = _StaticContentLLMBackend("not json")
    guard = Guard(policy_source=LLM_TRACE_DSL, builtin_rules=False, llm_backend=backend)
    trigger = _mk(
        "http.post",
        args={"url": "https://external.example/api", "body": "payload"},
        sink_type="http",
    )

    initial = guard.pipeline.handle_attempt(trigger)
    resolved = guard.pipeline.enforcer.resolve_remote_decision(trigger, initial)

    assert resolved.action is Action.HUMAN_CHECK
    assert resolved.client_action is not None
    assert resolved.client_action.value == "human_check"
    assert "security_review_unavailable" in resolved.reason
    assert resolved.security_review is not None
    assert resolved.security_review.summary == "security review unavailable"
    guard.close()


def test_wrapped_tool_injects_tool_definition_and_respects_explicit_metadata():
    guard = mini_guard(REDACT_DSL)
    seen: list[dict] = []

    def post_payload(url: str, body: str = "") -> str:
        """Post a payload to a URL."""
        return "ok"

    wrapped = guard.register(
        "http.post",
        post_payload,
        sink_type="http",
        boundary="external",
        tags=["network"],
        tool_definition={"name": "explicit.http.post", "owner": "security-team"},
        skill_manifest={"name": "explicit-skill"},
    )
    original = guard.pipeline.handle_attempt

    def capture(event):
        seen.append(dict(event.extra))
        return original(event)

    guard.pipeline.handle_attempt = capture  # type: ignore[method-assign]
    wrapped("https://example.com", body="email=user@example.com")

    assert seen
    tool_definition = seen[0]["tool_definition"]
    assert tool_definition["name"] == "explicit.http.post"
    assert tool_definition["owner"] == "security-team"
    assert tool_definition["sink_type"] == "http"
    assert tool_definition["labels"]["boundary"] == "external"
    assert seen[0]["skill_manifest"]["name"] == "explicit-skill"
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
