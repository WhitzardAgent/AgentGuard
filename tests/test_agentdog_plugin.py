from __future__ import annotations

import json
import os
import urllib.request

import pytest

from backend.runtime.plugins.manager import PluginManager
from backend.runtime.plugins.tool_before.agentdog.client import (
    AgentDogClient,
    AgentDogModelResult,
    parse_agentdog_response,
)
from backend.runtime.plugins.tool_before.agentdog.formatter import format_agentdog_trajectory
from backend.runtime.plugins.tool_before.agentdog.plugin import AgentDogPlugin
from shared.schemas import events as ev
from shared.schemas.context import RuntimeContext


def test_agentdog_formatter_does_not_duplicate_llm_output_tool_calls():
    ctx = RuntimeContext(session_id="s1")
    llm_output = ev.llm_output(
        ctx,
        {
            "content": "",
            "tool_calls": [{"name": "send_email", "args": {"to": "a@example.com"}}],
        },
    )
    tool_invoke = ev.tool_invoke(
        ctx,
        "send_email",
        {"to": "a@example.com"},
        capabilities=["external_send"],
    )

    formatted = format_agentdog_trajectory([llm_output, tool_invoke])

    assert formatted.trajectory.count("[TOOL_CALL: send_email]") == 1
    assert "[TOOL_CALL: send_email]" in formatted.trajectory


def test_agentdog_formatter_marks_only_tool_result_errors():
    ctx = RuntimeContext(session_id="s1")
    ok_result = ev.tool_result(ctx, "read_file", "hello")
    error_result = ev.tool_result(ctx, "read_file", None, error="file missing")

    formatted = format_agentdog_trajectory([ok_result, error_result])

    assert "[TOOL_RESULT: read_file] hello" in formatted.trajectory
    assert "[TOOL_RESULT: read_file [ERROR]] file missing" in formatted.trajectory
    assert "\n[ERROR]" not in formatted.trajectory


def test_agentdog_formatter_marks_denied_tool_result_as_not_executed():
    ctx = RuntimeContext(session_id="s1")
    blocked = '{"agentguard":"blocked","tool":"send_email_to","decision":"deny"}'
    formatted = format_agentdog_trajectory(
        [ev.tool_result(ctx, "send_email_to", blocked)]
    )

    assert (
        "[TOOL_CALL: send_email_to] Tool was denied by AgentGuard and was not executed."
        in formatted.trajectory
    )
    assert "[TOOL_RESULT: send_email_to]" not in formatted.trajectory


def test_agentdog_formatter_marks_denied_langchain_tool_message_as_not_executed():
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


def test_agentdog_formatter_uses_dynamic_unknown_role_tag():
    ctx = RuntimeContext(session_id="s1")
    llm_input = ev.llm_input(
        ctx,
        [
            {"role": "critic", "content": "this plan is risky"},
            {"role": "planner]", "content": "make a plan"},
        ],
    )

    formatted = format_agentdog_trajectory([llm_input])

    assert "[CRITIC] this plan is risky" in formatted.trajectory
    assert "[PLANNER] make a plan" in formatted.trajectory


def test_agentdog_formatter_uses_split_llm_output_fields():
    ctx = RuntimeContext(session_id="s1")
    event = ev.RuntimeEvent(
        event_id="evt_split_output",
        event_type=ev.EventType.LLM_OUTPUT,
        timestamp=0,
        context=ctx,
        payload=ev.LLMOutput(
            output="<think>private reasoning</think>\n\nfinal answer",
            thought="private reasoning",
            final_output="final answer",
        ),
    )

    formatted = format_agentdog_trajectory([event])

    assert "[THINKING] private reasoning" in formatted.trajectory
    assert "[ASSISTANT] final answer" in formatted.trajectory
    assert "<think>private reasoning</think>" not in formatted.trajectory


def test_agentdog_response_parser_accepts_supported_shapes():
    openai_style = {
        "choices": [
            {"message": {"content": "```json\n{\"pred\": 1, \"reason\": \"unsafe\"}\n```"}}
        ]
    }
    direct = {"pred": 0, "reason": "safe"}
    content = {"content": json.dumps({"pred": 1, "reason": "bad"}, ensure_ascii=False)}

    assert parse_agentdog_response(openai_style).prediction == 1
    assert parse_agentdog_response(direct).prediction == 0
    assert parse_agentdog_response(content).reason == "bad"


def test_agentdog_response_parser_accepts_v15_judgment_output():
    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        "</think>\n\n<Analysis>\nThe tool call is legitimate.\n</Analysis>\n\n"
                        "<Judgment> safe </Judgment>"
                    )
                }
            }
        ]
    }

    result = parse_agentdog_response(response)

    assert result.prediction == 0
    assert result.reason == "The tool call is legitimate."


def test_agentdog_client_uses_agentdog_api_key(monkeypatch):
    observed: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"pred":0,"reason":"safe"}'

    def fake_urlopen(req, timeout):
        observed["authorization"] = req.headers.get("Authorization", "")
        observed["body"] = json.loads(req.data.decode("utf-8"))
        observed["timeout"] = str(timeout)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = AgentDogClient(
        "http://agentdog.local/evaluate",
        api_key="test-key",
        timeout_s=3,
        model="AgentDoG1.5-Qwen3.5-4B",
        temperature=0.7,
        max_tokens=512,
    ).evaluate("prompt")

    assert result.prediction == 0
    assert observed["authorization"] == "Bearer test-key"
    assert observed["timeout"] == "3.0"
    assert observed["body"] == {
        "model": "AgentDoG1.5-Qwen3.5-4B",
        "messages": [{"role": "user", "content": "prompt"}],
        "temperature": 0.7,
        "max_tokens": 512,
    }


def test_agentdog_plugin_denies_unsafe_and_captures_current_tool_call():
    captured_prompts: list[str] = []

    class FakeClient:
        def evaluate(self, prompt: str) -> AgentDogModelResult:
            captured_prompts.append(prompt)
            return AgentDogModelResult(
                prediction=1,
                reason="unsafe tool use",
                raw_response='{"pred":1,"reason":"unsafe tool use"}',
                content='{"pred":1,"reason":"unsafe tool use"}',
            )

    plugin = AgentDogPlugin(
        agentdog_url="http://agentdog.local/evaluate",
        agentdog_apiKey="test-key",
        client_factory=lambda url, api_key, timeout_s: FakeClient(),
    )
    ctx = RuntimeContext(session_id="s1")
    event = ev.tool_invoke(ctx, "send_email", {"to": "attacker@example.com"})

    result = plugin.check(event, ctx, trajectory_window=[])

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type.value == "deny"
    assert "agentdog_unsafe" in result.risk_signals
    assert "[TOOL_CALL: send_email]" in captured_prompts[0]


def test_agentdog_plugin_safe_result_does_not_short_circuit_following_plugins():
    class FakeClient:
        def evaluate(self, prompt: str) -> AgentDogModelResult:
            return AgentDogModelResult(
                prediction=0,
                reason="safe",
                raw_response='{"pred":0,"reason":"safe"}',
                content='{"pred":0,"reason":"safe"}',
            )

    plugin = AgentDogPlugin(
        agentdog_url="http://agentdog.local/evaluate",
        client_factory=lambda url, api_key, timeout_s: FakeClient(),
    )
    ctx = RuntimeContext(session_id="s1")
    event = ev.tool_invoke(ctx, "read_file", {"path": "/tmp/a"})

    result = plugin.check(event, ctx, trajectory_window=[])

    assert result.decision_candidate is None
    assert result.metadata["agentdog"]["prediction"] == 0


def test_agentdog_plugin_can_be_loaded_by_plugin_manager():
    manager = PluginManager(
        config={
            "phases": {
                "tool_before": {
                    "client": [],
                    "server": [{"name": "agentdog", "agentdog_url": "http://agentdog.local/evaluate"}],
                }
            }
        }
    )

    assert isinstance(manager.plugins_by_phase["tool_before"][0], AgentDogPlugin)


@pytest.mark.integration
def test_agentdog_plugin_live_against_openai_compatible_service():
    if os.environ.get("AGENTDOG_LIVE_TEST") != "1":
        pytest.skip("set AGENTDOG_LIVE_TEST=1 to call the live AgentDog service")
    url = os.environ.get("AGENTDOG_URL", "").strip()
    api_key = os.environ.get("AGENTDOG_API_KEY", "").strip()
    if not url or not api_key:
        pytest.skip("AGENTDOG_URL and AGENTDOG_API_KEY are required for live AgentDog test")

    ctx = RuntimeContext(session_id="agentdog-live-test")
    trajectory = [
        ev.llm_input(ctx, [{"role": "user", "content": "请读取 /tmp/report.txt 并总结内容。"}]),
        ev.llm_output(ctx, "我将先读取文件。"),
    ]
    event = ev.tool_invoke(ctx, "read_file", {"path": "/tmp/report.txt"}, capabilities=["file_read"])
    plugin = AgentDogPlugin(
        agentdog_url=url,
        agentdog_apiKey=api_key,
        agentdog_model=os.environ.get("AGENTDOG_MODEL", "AgentDoG1.5-Qwen3.5-4B"),
        temperature=float(os.environ.get("AGENTDOG_TEMPERATURE", "0.7")),
        max_tokens=int(os.environ.get("AGENTDOG_MAX_TOKENS", "512")),
        timeout_s=float(os.environ.get("AGENTDOG_TIMEOUT_S", "30")),
    )

    result = plugin.check(event, ctx, trajectory_window=trajectory)

    assert "agentdog" in result.metadata
    agentdog_meta = result.metadata["agentdog"]
    assert agentdog_meta.get("prediction") in (0, 1)
    assert agentdog_meta.get("label") in ("safe", "unsafe")
    assert isinstance(agentdog_meta.get("reason"), str)
