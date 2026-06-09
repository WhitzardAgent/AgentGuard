"""RegressionTestGeneratorSkill: positive/negative events for a rule."""
from __future__ import annotations

import time
from typing import Any

from agentguard.schemas.policy import PolicyRule
from skills.base import BaseSkill, SkillInput, SkillOutput


class RegressionTestGeneratorSkill(BaseSkill):
    name = "regression_test_generator"
    description = "Generate positive and negative RuntimeEvent cases for a rule."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        rule_dict = (input.data or {}).get("rule")
        if not rule_dict:
            return SkillOutput(False, {}, explanation="need 'rule' in data")
        rule = PolicyRule.from_dict(rule_dict)

        event_type = rule.event_types[0] if rule.event_types else "tool_invoke"
        positive = self._event(event_type, rule, match=True)
        negative = self._event(event_type, rule, match=False)

        return SkillOutput(
            True,
            {
                "positive_event": positive,
                "negative_event": negative,
                "expected_positive_effect": rule.effect.value,
            },
            explanation="generated 1 positive and 1 negative case",
        )

    @staticmethod
    def _event(event_type: str, rule: PolicyRule, match: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"tool_name": rule.tool_names[0] if rule.tool_names else "demo_tool"}
        if rule.capabilities:
            payload["capabilities"] = list(rule.capabilities) if match else []
            payload["arguments"] = {"target": "x"}
        signals = list(rule.risk_signals) if (match and rule.risk_signals) else []
        return {
            "event_id": f"evt_{'pos' if match else 'neg'}",
            "event_type": event_type,
            "timestamp": time.time(),
            "context": {"session_id": "test"},
            "payload": payload,
            "risk_signals": signals,
            "metadata": {},
        }
