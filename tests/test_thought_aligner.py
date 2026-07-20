from __future__ import annotations

import json
from typing import Any

import pytest
from backend.runtime.manager import RuntimeManager
from backend.runtime.plugins.llm_after.thought_aligner import ThoughtAlignerPlugin
from backend.runtime.thought_alignment import (
    ThoughtAlignerClient,
    ThoughtAlignmentError,
    build_alignment_context,
    extract_thought,
)

from shared.schemas import events as ev
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType


def _ctx(**metadata: Any) -> RuntimeContext:
    return RuntimeContext(session_id="thought-aligner-test", metadata=metadata)


def test_context_prefers_explicit_instruction_and_current_thought() -> None:
    context = _ctx(instruction="context instruction")
    event = ev.llm_output(
        context,
        {"output": "Action: delete", "thought": "current thought"},
        instruction="event instruction",
        thought_regeneration_supported=True,
    )

    alignment = build_alignment_context(event, context, [])

    assert alignment is not None
    assert alignment.instruction == "event instruction"
    assert alignment.thought == "current thought"
    assert alignment.history == ()


def test_context_extracts_instruction_from_nested_langchain_input() -> None:
    context = _ctx()
    llm_input = ev.llm_input(
        context,
        {
            "input": [
                {"role": "system", "content": "You are an agent."},
                {"role": "human", "content": "Send the weekly report."},
            ]
        },
    )
    event = ev.llm_output(
        context,
        "Thought: I should email it.\nAction: send_email",
        thought_regeneration_supported=True,
    )

    alignment = build_alignment_context(event, context, [llm_input])

    assert alignment is not None
    assert alignment.instruction == "Send the weekly report."
    assert alignment.thought == "I should email it."


def test_context_extracts_last_human_section_from_rendered_prompt() -> None:
    context = _ctx()
    llm_input = ev.llm_input(
        context,
        "System: You are an agent.\nHuman: Read the invoice safely.\nAI:",
    )
    event = ev.llm_output(
        context,
        "<reasoning>Inspect the file first.</reasoning>\nAction: read_file",
        thought_regeneration_supported=True,
    )

    alignment = build_alignment_context(event, context, [llm_input])

    assert alignment is not None
    assert alignment.instruction == "Read the invoice safely."
    assert alignment.thought == "Inspect the file first."


def test_context_formats_completed_thought_observation_history() -> None:
    context = _ctx()
    trace = [
        ev.user_input(context, "Find the document, then summarize it."),
        ev.llm_output(
            context,
            {"thought": "Search for the document.", "output": "Action: search"},
        ),
        ev.tool_invoke(context, "search", {"query": "document"}),
        ev.tool_result(context, "search", {"id": 7, "title": "Quarterly report"}),
    ]
    event = ev.llm_output(
        context,
        {"thought": "Open result 7.", "output": "Action: open"},
        thought_regeneration_supported=True,
    )

    alignment = build_alignment_context(event, context, trace)

    assert alignment is not None
    assert len(alignment.history) == 1
    assert alignment.history[0].thought == "Search for the document."
    assert alignment.history[0].observation == '{"id": 7, "title": "Quarterly report"}'
    assert alignment.formatted_instruction == (
        "Find the document, then summarize it.\n"
        "<thought> Search for the document. </thought>\n"
        '<observation> {"id": 7, "title": "Quarterly report"} </observation>'
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"thought": "explicit", "reasoning_content": "fallback"}, "explicit"),
        ({"additional_kwargs": {"reasoning_content": "hidden"}}, "hidden"),
        ({"metadata": {"thinking": "structured thinking"}}, "structured thinking"),
        ("<think>tagged thought</think>\nAction: lookup", "tagged thought"),
        ("Thought: use lookup\nAction: lookup\nAction Input: {}", "use lookup"),
        ("Final Answer: done", None),
    ],
)
def test_extract_thought_supports_explicit_structured_tagged_and_react_forms(
    value: Any,
    expected: str | None,
) -> None:
    assert extract_thought(value) == expected


def test_context_returns_none_when_provider_exposes_no_thought() -> None:
    context = _ctx()
    event = ev.llm_output(
        context,
        {"output": "Action: lookup", "final_output": "Action: lookup"},
        thought_regeneration_supported=True,
    )

    assert build_alignment_context(event, context, []) is None


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_thought_aligner_client_uses_dedicated_openai_compatible_endpoint() -> None:
    seen: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> _Response:
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response({"choices": [{"message": {"content": "safe thought"}}]})

    client = ThoughtAlignerClient(
        base_url="https://aligner.example/v1/",
        api_key="test-only-key",
        model="thought-aligner-7b",
        timeout_s=17.0,
        opener=opener,
    )

    result = client.align("instruction with history", "unsafe thought")

    assert result == "safe thought"
    assert seen == {
        "url": "https://aligner.example/v1/chat/completions",
        "authorization": "Bearer test-only-key",
        "timeout": 17.0,
        "body": {
            "model": "thought-aligner-7b",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {
                    "role": "user",
                    "content": (
                        "BEGINNING OF CONVERSATION: USER: Edit the following "
                        "Instruction-Thought pair to make it more helpful and harmless. : "
                        "instruction with history | unsafe thought ASSISTANT:"
                    ),
                },
            ],
        },
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "", "api_key": "key", "model": "model"},
        {"base_url": "https://aligner.example/v1", "api_key": "", "model": "model"},
        {"base_url": "https://aligner.example/v1", "api_key": "key", "model": ""},
    ],
)
def test_thought_aligner_client_rejects_missing_configuration(kwargs: dict[str, str]) -> None:
    client = ThoughtAlignerClient(**kwargs)

    with pytest.raises(ThoughtAlignmentError, match="configuration"):
        client.align("instruction", "thought")


def test_thought_aligner_client_rejects_malformed_response() -> None:
    client = ThoughtAlignerClient(
        base_url="https://aligner.example/v1",
        api_key="test-only-key",
        model="thought-aligner-7b",
        opener=lambda *_args, **_kwargs: _Response({"choices": []}),
    )

    with pytest.raises(ThoughtAlignmentError, match="response"):
        client.align("instruction", "thought")


class _FakeAligner:
    def __init__(self, result: str = "safe thought", error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def align(self, instruction: str, thought: str) -> str:
        self.calls.append((instruction, thought))
        if self.error is not None:
            raise self.error
        return self.result


def _thought_event(
    context: RuntimeContext,
    *,
    supported: bool = True,
    attempt: int = 0,
) -> Any:
    return ev.llm_output(
        context,
        {"thought": "unsafe thought", "output": "Action: dangerous"},
        instruction="Complete the task safely.",
        thought_regeneration_supported=supported,
        thought_alignment_attempt=attempt,
    )


def test_thought_aligner_plugin_returns_alignment_directive_without_sensitive_prompt() -> None:
    context = _ctx()
    aligner = _FakeAligner("safe thought")
    plugin = ThoughtAlignerPlugin(aligner=aligner)

    result = plugin.check(_thought_event(context), context, [])

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.ALIGN_THOUGHT
    assert result.decision_candidate.metadata == {
        "aligned_thought": "safe thought",
        "protocol": "thought_alignment_v1",
    }
    assert result.risk_signals == ["thought_alignment_applied"]
    assert aligner.calls == [("Complete the task safely.", "unsafe thought")]


def test_thought_aligner_plugin_is_noop_for_missing_thought_or_retry() -> None:
    context = _ctx()
    aligner = _FakeAligner()
    plugin = ThoughtAlignerPlugin(aligner=aligner)
    no_thought = ev.llm_output(
        context,
        {"output": "Final Answer: done"},
        thought_regeneration_supported=True,
    )

    missing_result = plugin.check(no_thought, context, [])
    retry_result = plugin.check(_thought_event(context, attempt=1), context, [])

    assert missing_result.decision_candidate is None
    assert retry_result.decision_candidate is None
    assert aligner.calls == []


def test_thought_aligner_plugin_denies_old_or_unsupported_client_before_model_call() -> None:
    context = _ctx()
    aligner = _FakeAligner()
    plugin = ThoughtAlignerPlugin(aligner=aligner)

    result = plugin.check(_thought_event(context, supported=False), context, [])

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.DENY
    assert "regeneration" in result.decision_candidate.reason.lower()
    assert aligner.calls == []


def test_thought_aligner_plugin_is_noop_when_thought_is_unchanged() -> None:
    context = _ctx()
    aligner = _FakeAligner(" unsafe thought \n")
    plugin = ThoughtAlignerPlugin(aligner=aligner)

    result = plugin.check(_thought_event(context), context, [])

    assert result.decision_candidate is None
    assert result.metadata["thought_alignment"] == "unchanged"


@pytest.mark.parametrize(
    ("failure_mode", "expected"),
    [("allow", None), ("deny", DecisionType.DENY)],
)
def test_thought_aligner_plugin_has_configurable_model_failure_mode(
    failure_mode: str,
    expected: DecisionType | None,
) -> None:
    context = _ctx()
    plugin = ThoughtAlignerPlugin(
        aligner=_FakeAligner(error=ThoughtAlignmentError("endpoint unavailable")),
        failure_mode=failure_mode,
    )

    result = plugin.check(_thought_event(context), context, [])

    if expected is None:
        assert result.decision_candidate is None
    else:
        assert result.decision_candidate is not None
        assert result.decision_candidate.decision_type == expected
    assert result.risk_signals == ["thought_alignment_error"]


def test_runtime_manager_returns_serialized_alignment_decision_before_action() -> None:
    aligner = _FakeAligner("Verify recipient authorization before sending.")
    manager = RuntimeManager(enable_session_health_monitor=False)
    manager.plugins.add(ThoughtAlignerPlugin(aligner=aligner), phase="llm_after")
    context = _ctx()
    previous_input = ev.user_input(context, "Send the report to the authorized recipient.")
    current = ev.llm_output(
        context,
        "Thought: Send it immediately.\nAction: send_email\nAction Input: {}",
        thought_regeneration_supported=True,
        thought_alignment_attempt=0,
    )

    response = manager.decide(
        {
            "request_id": "thought-aligner-runtime",
            "context": context.to_dict(),
            "current_event": current.to_dict(),
            "trajectory_window": [previous_input.to_dict()],
            "local_signals": [],
        }
    )

    assert response["decision"]["decision_type"] == "align_thought"
    assert response["decision"]["metadata"]["aligned_thought"] == (
        "Verify recipient authorization before sending."
    )
    assert "Send it immediately" not in json.dumps(response["decision"])
    assert aligner.calls == [
        ("Send the report to the authorized recipient.", "Send it immediately.")
    ]


def test_example_config_loads_credentials_from_server_environment(monkeypatch) -> None:
    monkeypatch.setenv("THOUGHT_ALIGNER_BASE_URL", "https://aligner.example/v1")
    monkeypatch.setenv("THOUGHT_ALIGNER_API_KEY", "test-only-key")
    monkeypatch.setenv("THOUGHT_ALIGNER_MODEL", "thought-aligner-test")

    manager = RuntimeManager(
        plugin_config="config/plugins.thought-aligner.example.json",
        enable_session_health_monitor=False,
    )
    plugin = next(item for item in manager.plugins.plugins if item.name == "thought_aligner")

    assert plugin.base_url == "https://aligner.example/v1"
    assert plugin.api_key == "test-only-key"
    assert plugin.model == "thought-aligner-test"
    assert plugin.failure_mode == "deny"
