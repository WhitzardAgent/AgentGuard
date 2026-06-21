from __future__ import annotations

from skills.base import SkillInput
from skills.registry import get_registry


def test_dsl_writer_generates_rule():
    skill = get_registry().get("dsl_writer")
    out = skill.run(SkillInput(instruction="block external send when a secret is present"))
    assert out.success
    rules = out.result["rules"]
    assert rules and rules[0]["effect"] == "deny"


def test_rule_linter_flags_invalid_effect():
    skill = get_registry().get("rule_linter")
    out = skill.run(SkillInput(data={"rules": [{"rule_id": "r1", "effect": "nope"}]}))
    assert out.success is False
    assert any(i["level"] == "error" for i in out.result["issues"])


def test_rule_linter_passes_valid_rule():
    skill = get_registry().get("rule_linter")
    rule = {
        "rule_id": "r1",
        "effect": "deny",
        "reason": "x",
        "event_types": ["tool_invoke"],
        "capabilities": ["external_send"],
    }
    out = skill.run(SkillInput(data={"rules": [rule]}))
    assert out.success is True
