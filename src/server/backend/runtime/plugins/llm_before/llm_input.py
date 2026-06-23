"""Plugin for user/LLM input events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.common.patterns import find_signals, text_of
from backend.runtime.plugins.registry import register


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

        metadata = {"matched_prompt_templates": matched_templates} if matched_templates else {}
        return CheckResult(
            decision_candidate=GuardDecision.deny(
                "Prompt blocked by local llm_input plugin.",
                policy_id="local:llm_input:jailbreak_detected",
                risk_signals=list(signals),
                metadata=metadata,
            ),
            risk_signals=signals,
            metadata=metadata,
        )

