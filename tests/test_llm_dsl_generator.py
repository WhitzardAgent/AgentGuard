from __future__ import annotations

import json
from pathlib import Path

from shared.rules.llm_dsl_generator import (
    LLMRuleGeneratorWorkflow,
    RuleGenerationRequest,
)


class _FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("unexpected extra LLM call")
        return self._responses.pop(0)


def _tool_catalog() -> list[dict[str, object]]:
    return [
        {
            "owner_agent_id": "agent-alpha",
            "name": "email.send",
            "tool_key": "agent-alpha::email.send",
            "input_params": ["to", "subject", "body"],
            "labels": {
                "boundary": "external",
                "sensitivity": "moderate",
                "integrity": "trusted",
                "tags": ["notification"],
            },
        },
        {
            "owner_agent_id": "agent-alpha",
            "name": "search.docs",
            "tool_key": "agent-alpha::search.docs",
            "input_params": ["query"],
            "labels": {
                "boundary": "internal",
                "sensitivity": "low",
                "integrity": "trusted",
                "tags": ["read_only"],
            },
        },
    ]


def _good_payload(*, reason: str = "Block external email", prompt: str = "") -> str:
    dsl_lines = [
        "RULE: review_external_email",
        "ON: tool_call.requested(email.send)",
        'CONDITION: tool.boundary == "external"',
        "POLICY: DENY" if not prompt else "POLICY: LLM_CHECK",
    ]
    if prompt:
        dsl_lines.append(f'Prompt: "{prompt}"')
    dsl_lines.append(f'Reason: "{reason}"')
    payload = {
        "summary": "generated",
        "assumptions": ["email.send handles outbound mail"],
        "warnings": [],
        "rules": "\n".join(dsl_lines),
    }
    return json.dumps(payload, ensure_ascii=False)


def test_generate_accepts_first_valid_candidate() -> None:
    client = _FakeLLMClient([_good_payload()])
    workflow = LLMRuleGeneratorWorkflow(llm_client=client)

    session = workflow.generate(
        RuleGenerationRequest(
            user_requirement="禁止 agent-alpha 对外发送邮件",
            agent_id="agent-alpha",
            tool_catalog=_tool_catalog(),
        )
    )

    assert session.stop_reason == "ready_for_user_review"
    assert session.accepted_candidate is not None
    assert len(session.attempts) == 1
    assert session.accepted_candidate.validation.ok is True
    assert '"name": "email.send"' in client.prompts[0]


def test_generate_repairs_after_validation_failure() -> None:
    bad_payload = json.dumps(
        {
            "summary": "generated",
            "assumptions": [],
            "warnings": [],
            "rules": (
                "RULE: review_external_email\n"
                "ON: tool_call.requested(imaginary.tool)\n"
                'CONDITION: tool.boundary == "external"\n'
                "POLICY: DENY\n"
                'Reason: "Block external email"'
            ),
        },
        ensure_ascii=False,
    )
    client = _FakeLLMClient([bad_payload, _good_payload()])
    workflow = LLMRuleGeneratorWorkflow(llm_client=client)

    session = workflow.generate(
        RuleGenerationRequest(
            user_requirement="禁止 agent-alpha 对外发送邮件",
            agent_id="agent-alpha",
            tool_catalog=_tool_catalog(),
            max_rounds=3,
        )
    )

    assert len(session.attempts) == 2
    assert session.attempts[0].validation.ok is False
    assert session.accepted_candidate is not None
    assert "上一轮输出未通过校验" in client.prompts[1]
    assert "unknown_tool_match" in client.prompts[1]


def test_refine_uses_user_feedback_and_revalidates() -> None:
    client = _FakeLLMClient(
        [
            _good_payload(),
            _good_payload(
                reason="Use LLM to inspect outbound email body",
                prompt="Decide allow, deny, or human_check for outbound email content and give a short reason.",
            ),
        ]
    )
    workflow = LLMRuleGeneratorWorkflow(llm_client=client)
    session = workflow.generate(
        RuleGenerationRequest(
            user_requirement="限制对外发邮件",
            agent_id="agent-alpha",
            tool_catalog=_tool_catalog(),
            max_rounds=4,
        )
    )

    updated = workflow.refine(session, "不要直接 deny，改成 LLM_CHECK 审查邮件内容")

    assert updated.accepted_candidate is not None
    assert len(updated.attempts) == 2
    assert updated.user_feedback_history == ["不要直接 deny，改成 LLM_CHECK 审查邮件内容"]
    assert "用户修改意见" in client.prompts[1]
    assert "当前已通过校验的候选规则" in client.prompts[1]
    normalized = updated.accepted_candidate.validation.normalized_rules[0]
    assert normalized.effect.value == "require_remote_review"
    assert normalized.metadata["llm_prompt"].startswith("Decide allow, deny, or human_check")


def test_generate_writes_debug_logs(tmp_path: Path) -> None:
    client = _FakeLLMClient([_good_payload()])
    workflow = LLMRuleGeneratorWorkflow(llm_client=client, debug_log_dir=tmp_path)

    workflow.generate(
        RuleGenerationRequest(
            user_requirement="禁止 agent-alpha 对外发送邮件",
            agent_id="agent-alpha",
            tool_catalog=_tool_catalog(),
        )
    )

    logs = sorted(tmp_path.glob("*.json"))
    assert len(logs) == 2
    request_log = json.loads(logs[0].read_text(encoding="utf-8"))
    response_log = json.loads(logs[1].read_text(encoding="utf-8"))
    assert request_log["phase"] == "request"
    assert "prompt" in request_log
    assert response_log["phase"] == "response"
    assert "raw_response" in response_log
