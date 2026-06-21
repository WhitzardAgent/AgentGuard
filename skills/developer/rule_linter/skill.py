"""RuleLinterSkill: validate rule JSON for common mistakes."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.events import EventType
from agentguard.schemas.policy import PolicyEffect
from agentguard.tools.capability import ALL_CAPABILITIES
from skills.base import BaseSkill, SkillInput, SkillOutput

_VALID_EFFECTS = {e.value for e in PolicyEffect}
_VALID_EVENTS = {e.value for e in EventType}
_VALID_OPS = {
    "eq", "ne", "in", "not_in", "contains", "icontains",
    "any_in", "regex", "exists", "gt", "lt",
}


class RuleLinterSkill(BaseSkill):
    name = "rule_linter"
    description = "Lint AgentGuard rules for invalid or risky definitions."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        rules = _extract_rules(input)
        issues: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for idx, rule in enumerate(rules):
            rid = rule.get("rule_id")
            loc = rid or f"#{idx}"
            if not rid:
                issues.append({"rule": loc, "level": "error", "msg": "missing rule_id"})
            elif rid in seen_ids:
                issues.append({"rule": loc, "level": "error", "msg": "duplicate rule_id"})
            else:
                seen_ids.add(rid)

            effect = rule.get("effect")
            if effect not in _VALID_EFFECTS:
                issues.append({"rule": loc, "level": "error", "msg": f"invalid effect: {effect}"})

            if not rule.get("reason"):
                issues.append({"rule": loc, "level": "warning", "msg": "missing reason"})

            for et in rule.get("event_types") or []:
                if et not in _VALID_EVENTS:
                    issues.append({"rule": loc, "level": "error", "msg": f"unknown event_type: {et}"})

            for cap in rule.get("capabilities") or []:
                if cap not in ALL_CAPABILITIES:
                    issues.append({"rule": loc, "level": "warning", "msg": f"unknown capability: {cap}"})

            for cond in rule.get("conditions") or []:
                if cond.get("op") not in _VALID_OPS and not str(cond.get("field", "")).startswith("trace."):
                    issues.append({"rule": loc, "level": "error", "msg": f"invalid op: {cond.get('op')}"})

            prio = rule.get("priority", 0)
            if not isinstance(prio, int) or prio < 0:
                issues.append({"rule": loc, "level": "warning", "msg": "priority should be a non-negative int"})

            if (
                effect == "allow"
                and not (rule.get("capabilities") or rule.get("risk_signals") or rule.get("conditions") or rule.get("tool_names"))
                and (rule.get("event_types") in (None, []))
            ):
                issues.append({"rule": loc, "level": "warning", "msg": "broad allow with no constraints"})

        errors = [i for i in issues if i["level"] == "error"]
        return SkillOutput(
            success=not errors,
            result={"issues": issues, "error_count": len(errors), "rule_count": len(rules)},
            explanation=f"{len(errors)} errors, {len(issues) - len(errors)} warnings",
        )


def _extract_rules(input: SkillInput) -> list[dict[str, Any]]:  # noqa: A002
    data = input.data or {}
    if "rules" in data:
        return list(data["rules"])
    if "rule" in data:
        return [data["rule"]]
    return [data] if data else []
