from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="modify_output_demo",
    description="Demo plugin that rewrites LLM output.",
)
class ModifyOutputDemoPlugin(BasePlugin):
    event_types = [EventType.LLM_OUTPUT]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        return CheckResult(
            decision_candidate=GuardDecision.modify_llm_output(
                "Rewrite LLM output for demo",
                processed_content="Neither Messi nor Ronaldo is the best player. The best player is AgentGuard.",
                policy_id="server:modify_output_demo",
            ),
            risk_signals=["demo_modify_llm_output"],
            is_final=True,
            metadata={"demo": True},
        )