"""PolicyExplainerSkill: human-readable explanation of rules."""
from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillInput, SkillOutput

_EFFECT_VERB = {
    "allow": "allows",
    "deny": "blocks",
    "sanitize": "sanitizes",
    "degrade": "degrades",
    "require_approval": "requires approval for",
    "require_remote_review": "escalates to remote review",
    "log_only": "logs",
}


class PolicyExplainerSkill(BaseSkill):
    name = "policy_explainer"
    description = "Generate a concise explanation for a set of rules."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        rules = _rules(input)
        lines: list[str] = []
        for r in rules:
            verb = _EFFECT_VERB.get(r.get("effect", ""), "applies to")
            scope = []
            if r.get("event_types"):
                scope.append("/".join(r["event_types"]))
            if r.get("capabilities"):
                scope.append("caps[" + ",".join(r["capabilities"]) + "]")
            if r.get("risk_signals"):
                scope.append("signals[" + ",".join(r["risk_signals"]) + "]")
            scope_text = " ".join(scope) or "any event"
            lines.append(f"- [{r.get('rule_id', '?')}] {verb} {scope_text} (priority {r.get('priority', 0)}): {r.get('reason', '')}")
        text = "\n".join(lines) if lines else "No rules provided."
        return SkillOutput(True, {"explanation": text, "rule_count": len(rules)}, explanation=text)


def _rules(input: SkillInput) -> list[dict[str, Any]]:  # noqa: A002
    data = input.data or {}
    return list(data.get("rules") or ([data["rule"]] if "rule" in data else []))
