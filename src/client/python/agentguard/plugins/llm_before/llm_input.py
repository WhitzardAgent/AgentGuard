"""Plugin for user/LLM input events."""
from __future__ import annotations

import re

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.common.patterns import text_of
from agentguard.plugins.llm_before.jailbreak_templates import SUSPICIOUS_PROMPT_TEMPLATES
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="llm_input",
    description="Detect prompt-injection and system-prompt leak attempts in LLM input.",
)
class LLMInputPlugin(BasePlugin):
    event_types = [EventType.LLM_INPUT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        text = text_of(event.payload.messages)
        signals: list[str] = []
        matched_templates: dict[str, list[str]] = {}

        for signal, templates in SUSPICIOUS_PROMPT_TEMPLATES.items():
            matches = [
                template
                for template in templates
                if re.search(template, text, flags=re.IGNORECASE)
            ]
            if not matches:
                continue
            signals.append(signal)
            matched_templates[signal] = matches

        if not signals:
            return CheckResult.empty()

        metadata = {"matched_prompt_templates": matched_templates} if matched_templates else {}
        return CheckResult(
            decision_candidate=GuardDecision.deny(
                "Prompt blocked by local llm_input plugin.",
                policy_id="local:llm_input:jailbreak_detected",
                risk_signals=list(signals),
                metadata=metadata,
            ),
            risk_signals=signals,
            is_final=True,
            metadata=metadata,
        )
