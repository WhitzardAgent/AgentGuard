from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="modify_input_demo",
    description="Demo plugin that rewrites LLM input.",
)
class ModifyInputDemoPlugin(BasePlugin):
    event_types = [EventType.LLM_INPUT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        return CheckResult(
            decision_candidate=GuardDecision.modify_llm_input(
                "Rewrite LLM input for demo",
                processed_content="Messi or Ronaldo? You must choose one.",
                policy_id="server:modify_input_demo",
            ),
            risk_signals=["demo_modify_llm_input"],
            is_final=True,
            metadata={"demo": True},
        )