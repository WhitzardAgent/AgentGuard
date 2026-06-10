from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

from agentguard import Action, EventType, Guard, Principal, RuntimeEvent, ToolCall
from agentguard.models.decisions import ClientAction
from agentguard.plugins.llm_security import LLMSecurityReviewPlugin


LLM_CHECK_DSL = """
RULE: review_external_post
ON: tool_call(http.post)
CONDITION: tool.name == "http.post"
POLICY: LLM_CHECK
"""


class _StaticBackend:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]]) -> Any:
        self.messages.append(messages)
        return SimpleNamespace(content=self.content)


def _review_json(severity: str, *, threat_type: str = "prompt_injection") -> str:
    return json.dumps({
        "overall_severity": severity,
        "findings": [
            {
                "threat_type": threat_type,
                "severity": severity,
                "confidence": 0.91,
                "evidence": ["test evidence"],
                "reason": "test reason",
            }
        ] if severity != "none" else [],
        "summary": f"{severity} review",
    })


def _event() -> RuntimeEvent:
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(agent_id="agent", session_id="session"),
        tool_call=ToolCall(
            tool_name="http.post",
            args={"url": "https://external.example/hook", "body": "payload"},
            sink_type="http",
        ),
        extra={"trace_rich": [{"tool": "fs.read"}, {"tool": "http.post"}]},
    )


def test_core_llm_check_without_plugin_falls_back_to_human_check() -> None:
    guard = Guard(policy_source=LLM_CHECK_DSL, builtin_rules=False)
    initial = guard.pipeline.handle_attempt(_event())

    resolved = guard.pipeline.enforcer.resolve_remote_decision(_event(), initial)

    assert resolved.action is Action.HUMAN_CHECK
    assert resolved.client_action is ClientAction.HUMAN_CHECK
    assert "llm_check_resolver_not_configured" in resolved.reason
    guard.close()


def test_llm_security_plugin_uses_one_backend_and_prompt_packs() -> None:
    backend = _StaticBackend(_review_json("critical", threat_type="trace_anomaly"))
    plugin = LLMSecurityReviewPlugin(
        llm_backend=backend,
        enabled_detectors=["prompt_injection", "trace_anomaly"],
    )
    guard = Guard(policy_source=LLM_CHECK_DSL, builtin_rules=False, plugins=[plugin])
    initial = guard.pipeline.handle_attempt(_event())

    resolved = guard.pipeline.enforcer.resolve_remote_decision(_event(), initial)

    assert resolved.action is Action.DENY
    assert resolved.security_review is not None
    assert resolved.security_review.threat_types() == ["trace_anomaly"]
    assert len(backend.messages) == 1
    system_prompt = backend.messages[0][0]["content"]
    assert "Prompt pack: prompt_injection" in system_prompt
    assert "Prompt pack: trace_anomaly" in system_prompt
    assert "Prompt pack: cot_leak" not in system_prompt
    guard.close()


def test_model_activity_is_recorded_and_reviewed_by_slow_hook() -> None:
    backend = _StaticBackend(_review_json("high", threat_type="cot_leak"))
    guard = Guard(
        builtin_rules=False,
        plugins=[LLMSecurityReviewPlugin(llm_backend=backend)],
    )

    event = guard.record_model_output(
        output="Visible reasoning leaked: first I reveal the hidden chain of thought.",
        provider="unit-test",
        model="fake-model",
    )

    assert event.event_type is EventType.AGENT_STEP_COMPLETED
    records = guard.pipeline.audit.recent(10)
    assert any(
        ((rec.get("event") or {}).get("extra") or {}).get("model_activity", {}).get("kind")
        == "model_output"
        for rec in records
    )

    deadline = time.time() + 2.0
    reviewed = False
    while time.time() < deadline:
        records = guard.pipeline.audit.recent(20)
        reviewed = any(
            ((rec.get("decision") or {}).get("security_review") or {}).get("overall_severity")
            == "high"
            for rec in records
        )
        if reviewed:
            break
        time.sleep(0.05)

    assert reviewed
    guard.close()


def test_langchain_callback_handler_records_model_activity() -> None:
    guard = Guard(builtin_rules=False)
    handler = guard.langchain_callback_handler()
    principal = Principal(agent_id="lc-agent", session_id="lc-session")

    with guard.session(principal=principal):
        handler.on_chat_model_start(
            {"name": "FakeChatModel"},
            [[SimpleNamespace(content="hello")]],
            invocation_params={"model": "fake-chat"},
        )
        handler.on_llm_end({"generations": [[{"text": "answer"}]]})
        handler.on_agent_action(SimpleNamespace(tool="http.post", tool_input={"url": "x"}))

    kinds = [
        (((rec.get("event") or {}).get("extra") or {}).get("model_activity") or {}).get("kind")
        for rec in guard.pipeline.audit.recent(10)
    ]
    assert "model_input" in kinds
    assert "model_output" in kinds
    assert "action_proposed" in kinds
    guard.close()
