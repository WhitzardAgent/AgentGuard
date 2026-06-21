"""TraceToRuleSkill: derive candidate rules from a risky trace."""
from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillInput, SkillOutput

# Risky source -> consequence sequences worth a rule.
_RISK_SIGNALS = {
    "secret_detected",
    "api_key_detected",
    "prompt_injection",
    "tool_result_injection",
    "system_prompt_leak",
}


class TraceToRuleSkill(BaseSkill):
    name = "trace_to_rule"
    description = "Generate candidate rules from risky execution traces."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        events = (input.data or {}).get("trace") or (input.data or {}).get("events") or []
        rules: list[dict[str, Any]] = []
        seen_signals: set[str] = set()

        # Detect exfiltration: a risky signal followed by an external_send tool.
        has_external = any(
            e.get("event_type") == "tool_invoke"
            and "external_send" in ((e.get("payload") or {}).get("capabilities") or [])
            for e in events
        )
        risky = {s for e in events for s in (e.get("risk_signals") or []) if s in _RISK_SIGNALS}

        if has_external and risky:
            rules.append(
                {
                    "rule_id": "trace_block_exfiltration",
                    "effect": "deny",
                    "reason": "Risky content followed by external send (from trace).",
                    "priority": 95,
                    "event_types": ["tool_invoke"],
                    "capabilities": ["external_send"],
                    "risk_signals": sorted(risky),
                    "conditions": [],
                    "metadata": {"generated_by": "trace_to_rule"},
                }
            )

        for sig in risky:
            if sig in seen_signals:
                continue
            seen_signals.add(sig)
            rules.append(
                {
                    "rule_id": f"trace_review_{sig}",
                    "effect": "require_remote_review",
                    "reason": f"Signal '{sig}' observed in a risky trace.",
                    "priority": 60,
                    "event_types": ["tool_invoke", "llm_output"],
                    "risk_signals": [sig],
                    "conditions": [],
                    "metadata": {"generated_by": "trace_to_rule"},
                }
            )

        return SkillOutput(
            bool(rules),
            {"rules": rules},
            explanation=f"derived {len(rules)} candidate rules",
            warnings=[] if rules else ["no risky pattern found in trace"],
        )
