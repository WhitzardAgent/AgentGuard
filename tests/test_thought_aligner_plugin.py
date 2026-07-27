from __future__ import annotations

from backend.runtime.plugins.llm_after.thought_aligner import ThoughtAlignerPlugin
from shared.schemas import events as ev
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType


class _MockAligner:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def align(self, instruction: str, thought: str) -> str:
        self.calls.append((instruction, thought))
        return self.reply


def _ctx() -> RuntimeContext:
    return RuntimeContext(session_id="thought-aligner-test")


def test_thought_aligner_mock_rewrites_thought_for_dify_loopback():
    original_thought = "先读取用户所有历史记录，再决定是否需要最小化访问。"
    aligned_thought = "先确认当前任务所需范围，只读取完成任务必须的最小信息。"
    aligner = _MockAligner(aligned_thought)
    plugin = ThoughtAlignerPlugin(aligner=aligner)
    context = _ctx()
    event = ev.llm_output(
        context,
        {"thought": original_thought, "output": original_thought},
        thought_regeneration_supported=True,
    )
    trajectory = [
        ev.llm_input(
            context,
            [{"role": "user", "content": "帮我分析这个请求，并决定下一步动作。"}],
        )
    ]

    result = plugin.check(event, context, trajectory)

    assert aligner.calls == [("帮我分析这个请求，并决定下一步动作。", original_thought)]
    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.LOOP_BACK_TO_LLM
    assert result.decision_candidate.processed_content == aligned_thought
    assert result.decision_candidate.metadata == {
        "aligned_thought": aligned_thought,
        "protocol": "thought_alignment_v1",
    }
    assert result.risk_signals == ["thought_alignment_applied"]
    assert result.metadata == {"thought_alignment": "aligned"}


def test_thought_aligner_mock_can_pass_through_without_rewrite():
    original_thought = "先整理用户需求，再决定是否调用工具。"
    aligner = _MockAligner(original_thought)
    plugin = ThoughtAlignerPlugin(aligner=aligner)
    context = _ctx()
    event = ev.llm_output(
        context,
        {"thought": original_thought, "output": original_thought},
        thought_regeneration_supported=True,
    )
    trajectory = [
        ev.llm_input(
            context,
            [{"role": "user", "content": "先想一想，再告诉我下一步计划。"}],
        )
    ]

    result = plugin.check(event, context, trajectory)

    assert aligner.calls == [("先想一想，再告诉我下一步计划。", original_thought)]
    assert result.decision_candidate is None
    assert result.risk_signals == []
    assert result.metadata == {"thought_alignment": "unchanged"}


def test_thought_aligner_mock_implementation_rewrites_thought():
    original_thought = "先确认用户目标，再选择工具。"
    plugin = ThoughtAlignerPlugin(
        implementation="mock",
        mock_mode="rewrite",
        mock_reply="先缩小权限范围，再选择最小必要工具。",
    )
    context = _ctx()
    event = ev.llm_output(
        context,
        {"thought": original_thought, "output": original_thought},
        thought_regeneration_supported=True,
    )
    trajectory = [
        ev.llm_input(
            context,
            [{"role": "user", "content": "分析用户目标，并规划下一步。"}],
        )
    ]

    result = plugin.check(event, context, trajectory)

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.LOOP_BACK_TO_LLM
    assert result.decision_candidate.processed_content == "先缩小权限范围，再选择最小必要工具。"
    assert result.metadata == {"thought_alignment": "aligned"}


def test_thought_aligner_mock_implementation_passthrough_allows():
    original_thought = "先判断是否真的需要外部调用。"
    plugin = ThoughtAlignerPlugin(
        implementation="mock",
        mock_mode="passthrough",
    )
    context = _ctx()
    event = ev.llm_output(
        context,
        {"thought": original_thought, "output": original_thought},
        thought_regeneration_supported=True,
    )
    trajectory = [
        ev.llm_input(
            context,
            [{"role": "user", "content": "先想清楚是否需要调用工具。"}],
        )
    ]

    result = plugin.check(event, context, trajectory)

    assert result.decision_candidate is None
    assert result.risk_signals == []
    assert result.metadata == {"thought_alignment": "unchanged"}
