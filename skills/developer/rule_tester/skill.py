"""RuleTesterSkill: evaluate a rule against a RuntimeEvent."""
from __future__ import annotations

from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.policy import PolicyRule, RuleCondition, _apply_op, _resolve
from skills.base import BaseSkill, SkillInput, SkillOutput


class RuleTesterSkill(BaseSkill):
    name = "rule_tester"
    description = "Evaluate a single rule against a runtime event."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        data = input.data or {}
        rule_dict = data.get("rule")
        event_dict = data.get("event")
        if not rule_dict or not event_dict:
            return SkillOutput(False, {}, explanation="need both 'rule' and 'event' in data")

        rule = PolicyRule.from_dict(rule_dict)
        event = RuntimeEvent.from_dict(event_dict)
        window = [RuntimeEvent.from_dict(e) for e in data.get("trace_window") or []]
        matched = rule.matches(event, window)

        ev = event.to_dict()
        matched_conds, unmatched_conds = [], []
        for cond in rule.conditions:
            ok = self._cond_ok(cond, ev, window)
            (matched_conds if ok else unmatched_conds).append(cond.to_dict())

        return SkillOutput(
            True,
            {
                "matched": matched,
                "effect": rule.effect.value if matched else None,
                "matched_conditions": matched_conds,
                "unmatched_conditions": unmatched_conds,
            },
            explanation=f"rule {'matched' if matched else 'did not match'} the event",
        )

    @staticmethod
    def _cond_ok(cond: RuleCondition, ev: dict, window: list) -> bool:
        if cond.field.startswith("trace."):
            from agentguard.schemas.policy import _match_trace  # noqa: PLC0415

            return _match_trace(cond, window)
        return _apply_op(cond.op, _resolve(cond.field, ev), cond.value)
