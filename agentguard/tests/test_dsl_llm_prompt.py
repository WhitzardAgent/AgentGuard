from __future__ import annotations

from agentguard.models.decisions import Action
from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall
from agentguard.policy.dsl.compiler import compile_rules
from agentguard.policy.evaluator.matcher import FastEvaluator


LLM_PROMPT_DSL = """
RULE: review-external
ON: tool_call(http.post)
CONDITION: args.url == "https://external.example/api"
POLICY: LLM_CHECK
Prompt: "Escalate ambiguous outbound HTTP requests."
Severity: high
Category: network
"""


def test_v3_prompt_metadata_is_preserved_for_llm_check():
    rules = compile_rules(LLM_PROMPT_DSL)
    assert rules[0].meta["prompt"] == "Escalate ambiguous outbound HTTP requests."


def test_llm_check_decision_carries_prompt_from_v3_rule():
    rules = compile_rules(LLM_PROMPT_DSL)
    ev = RuntimeEvent(
        event_type=EventType.TOOL_CALL_REQUESTED,
        principal=Principal(agent_id="a", session_id="s1", role="planner", trust_level=1),
        tool_call=ToolCall(
            tool_name="http.post",
            args={"url": "https://external.example/api"},
            target={},
            sink_type="http",
        ),
        scope=[],
        extra={},
    )
    decision = FastEvaluator(rules).evaluate(ev, {})
    assert decision.action == Action.LLM_CHECK
    assert decision.llm_system_prompt == "Escalate ambiguous outbound HTTP requests."
    assert decision.reason == "review-external"
