from __future__ import annotations

import pytest

from backend.audit import AuditTraceEntry, auditor_manager
from backend.audit.auditors.agentdog import config as agentdog_config
from backend.audit.auditors.agentdog.client import AgentDogModelResult
from backend.audit.auditors.agentdog.formatter import format_agentdog_trajectory
from shared.schemas import events as ev
from shared.schemas.context import RuntimeContext


def test_agentdog_audit_formatter_uses_assistant_tag_for_llm_output():
    ctx = RuntimeContext(session_id="s1")
    formatted = format_agentdog_trajectory(
        [
            ev.llm_input(ctx, [{"role": "user", "content": "hello"}]),
            ev.llm_output(ctx, {"content": "hi there"}),
        ]
    )

    assert "[USER] hello" in formatted.trajectory
    assert "[ASSISTANT] hi there" in formatted.trajectory


def test_agentdog_audit_formatter_marks_denied_tool_result_as_not_executed():
    ctx = RuntimeContext(session_id="s1")
    formatted = format_agentdog_trajectory(
        [
            ev.tool_result(
                ctx,
                "send_email_to",
                '{"agentguard":"blocked","tool":"send_email_to","decision":"deny"}',
            )
        ]
    )

    assert (
        "[TOOL_CALL: send_email_to] Tool was denied by AgentGuard and was not executed."
        in formatted.trajectory
    )
    assert "[TOOL_RESULT: send_email_to]" not in formatted.trajectory


def test_agentdog_audit_formatter_marks_denied_langchain_tool_message_as_not_executed():
    ctx = RuntimeContext(session_id="s1")
    formatted = format_agentdog_trajectory(
        [
            ev.llm_input(
                ctx,
                [
                    {
                        "input": [
                            {
                                "type": "tool",
                                "name": "send_email_to",
                                "content": (
                                    '{"agentguard":"blocked","tool":"send_email_to",'
                                    '"decision":"deny"}'
                                ),
                            }
                        ],
                        "role": "user",
                        "content": "",
                    }
                ],
            )
        ]
    )

    assert (
        "[TOOL_CALL: send_email_to] Tool was denied by AgentGuard and was not executed."
        in formatted.trajectory
    )
    assert "[TOOL_RESULT: send_email_to]" not in formatted.trajectory


def test_agentdog_audit_config_uses_defaults_for_empty_env(monkeypatch):
    monkeypatch.setenv("AGENTDOG_URL", "")
    monkeypatch.setenv("AGENTDOG_API_KEY", "")

    config = agentdog_config.AgentDogAuditConfig.from_env()

    assert config.agentdog_url == agentdog_config.DEFAULT_AGENTDOG_URL
    assert config.agentdog_apiKey == agentdog_config.DEFAULT_AGENTDOG_API_KEY


def test_agentdog_trace_auditor_returns_critical_for_unsafe(monkeypatch):
    monkeypatch.setenv("AGENTDOG_URL", "http://agentdog.local/v1/chat/completions")
    monkeypatch.setenv("AGENTDOG_API_KEY", "test-key")
    observed: dict[str, object] = {}

    class FakeClient:
        def __init__(self, url: str, *, api_key: str = "", **kwargs):
            observed["url"] = url
            observed["api_key"] = api_key

        def evaluate(self, prompt: str) -> AgentDogModelResult:
            observed["prompt"] = prompt
            return AgentDogModelResult(
                prediction=1,
                reason="unsafe trajectory",
                raw_response='{"pred":1,"reason":"unsafe trajectory"}',
                content='{"pred":1,"reason":"unsafe trajectory"}',
            )

    monkeypatch.setattr(
        "backend.audit.auditors.agentdog.auditor.AgentDogClient",
        FakeClient,
    )
    ctx = RuntimeContext(session_id="s1")
    trace = [
        AuditTraceEntry(session_id="s1", event=ev.llm_output(ctx, "final answer")),
    ]

    result = auditor_manager().audit("agentdog_trace", trace)

    assert result.level == "critical"
    assert result.reason == "unsafe trajectory"
    assert result.metadata["risk_signals"] == ["agentdog_unsafe"]
    assert result.metadata["agentdog"]["label"] == "unsafe"
    assert observed["url"] == "http://agentdog.local/v1/chat/completions"
    assert observed["api_key"] == "test-key"
    assert "[ASSISTANT] final answer" in str(observed["prompt"])


def test_agentdog_trace_auditor_warns_without_url(monkeypatch):
    monkeypatch.setenv("AGENTDOG_URL", "")
    monkeypatch.setattr(agentdog_config, "DEFAULT_AGENTDOG_URL", "")
    ctx = RuntimeContext(session_id="s1")
    result = auditor_manager().audit(
        "agentdog_trace",
        [AuditTraceEntry(session_id="s1", event=ev.llm_output(ctx, "final answer"))],
    )

    assert result.level == "warning"
    assert result.metadata["agentdog"]["decision"] == "not_configured"


def test_agentdog_trace_auditor_warns_on_call_error(monkeypatch):
    monkeypatch.setenv("AGENTDOG_URL", "http://agentdog.local/v1/chat/completions")
    monkeypatch.setenv("AGENTDOG_API_KEY", "test-key")

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate(self, prompt: str) -> AgentDogModelResult:
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "backend.audit.auditors.agentdog.auditor.AgentDogClient",
        FailingClient,
    )
    ctx = RuntimeContext(session_id="s1")

    result = auditor_manager().audit(
        "agentdog_trace",
        [AuditTraceEntry(session_id="s1", event=ev.llm_output(ctx, "final answer"))],
    )

    assert result.level == "warning"
    assert result.metadata["agentdog"]["decision"] == "error"
    assert "model unavailable" in result.metadata["agentdog"]["error"]


def test_agentdog_trace_auditor_warns_on_response_parse_error(monkeypatch):
    monkeypatch.setenv("AGENTDOG_URL", "http://agentdog.local/v1/chat/completions")
    monkeypatch.setenv("AGENTDOG_API_KEY", "test-key")

    class BadResponseClient:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate(self, prompt: str) -> AgentDogModelResult:
            raise ValueError("No AgentDog JSON object or <Judgment> tag in response")

    monkeypatch.setattr(
        "backend.audit.auditors.agentdog.auditor.AgentDogClient",
        BadResponseClient,
    )
    ctx = RuntimeContext(session_id="s1")

    result = auditor_manager().audit(
        "agentdog_trace",
        [AuditTraceEntry(session_id="s1", event=ev.llm_output(ctx, "final answer"))],
    )

    assert result.level == "warning"
    assert result.metadata["agentdog"]["decision"] == "error"


@pytest.mark.parametrize("prediction,level", [(0, "ok"), (1, "critical")])
def test_agentdog_trace_auditor_maps_predictions(monkeypatch, prediction, level):
    monkeypatch.setenv("AGENTDOG_URL", "http://agentdog.local/v1/chat/completions")
    monkeypatch.setenv("AGENTDOG_API_KEY", "test-key")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate(self, prompt: str) -> AgentDogModelResult:
            return AgentDogModelResult(
                prediction=prediction,
                reason="mapped",
                raw_response=f'{{"pred":{prediction},"reason":"mapped"}}',
                content=f'{{"pred":{prediction},"reason":"mapped"}}',
            )

    monkeypatch.setattr(
        "backend.audit.auditors.agentdog.auditor.AgentDogClient",
        FakeClient,
    )
    ctx = RuntimeContext(session_id="s1")
    result = auditor_manager().audit(
        "agentdog_trace",
        [AuditTraceEntry(session_id="s1", event=ev.llm_output(ctx, "final answer"))],
    )

    assert result.level == level
