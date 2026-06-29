"""Plugin for user/LLM input events."""
from __future__ import annotations

import re

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeEvent

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.common.patterns import text_of
from backend.runtime.plugins.llm_before.jailbreak_templates import SUSPICIOUS_PROMPT_TEMPLATES
from backend.runtime.plugins.registry import register


@register(
    name="jailbreak_check",
    description="Detect prompt-injection and system-prompt leak attempts in LLM input.",
)
class JailbreakCheckPlugin(BasePlugin):
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
                "Prompt blocked by local jailbreak_check plugin.",
                policy_id="local:jailbreak_check:jailbreak_detected",
                risk_signals=list(signals),
                metadata=metadata,
            ),
            risk_signals=signals,
            is_final=True,
            metadata=metadata,
        )
