"""Tests for the multi-pack rule router and YAML pack loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agentguard import Guard, Principal
from agentguard.policy.rules.pack_loader import (
    apply_rule_pack_config,
    load_rule_pack_config,
)
from agentguard.policy.routing import RuleRouter


OFFICE_RULES = """
RULE: allow_office_email
ON: tool_call(email.send)
CONDITION: principal.role == "basic"
POLICY: ALLOW
"""

DEV_RULES = """
RULE: deny_dev_shell
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: DENY
"""


@pytest.fixture
def guard() -> Guard:
    g = Guard(policy_source=None, builtin_rules=False, mode="enforce")
    yield g
    g.close()


def test_default_packs_present(guard: Guard) -> None:
    pack_ids = {p.pack_id for p in guard.list_rule_packs()}
    assert RuleRouter.BUILTIN_PACK_ID in pack_ids
    assert RuleRouter.DEFAULT_PACK_ID in pack_ids


def test_add_and_bind_rule_pack(guard: Guard) -> None:
    guard.add_rule_pack("office", OFFICE_RULES)
    guard.bind_agent("agent_office_001", "office")
    rule_ids = {r.rule_id for r in guard.rules_for_agent("agent_office_001")}
    assert "allow_office_email" in rule_ids
    rule_ids_other = {r.rule_id for r in guard.rules_for_agent("unbound_agent")}
    assert "allow_office_email" not in rule_ids_other


def test_unbound_agent_falls_back_to_default_pack(guard: Guard) -> None:
    guard.add_rules(OFFICE_RULES)
    rule_ids = {r.rule_id for r in guard.rules_for_agent("any_agent")}
    assert "allow_office_email" in rule_ids


def test_bound_agent_does_not_see_default_pack(guard: Guard) -> None:
    guard.add_rules(OFFICE_RULES)
    guard.add_rule_pack("dev", DEV_RULES)
    guard.bind_agent("dev_001", "dev")
    rule_ids = {r.rule_id for r in guard.rules_for_agent("dev_001")}
    assert "deny_dev_shell" in rule_ids
    assert "allow_office_email" not in rule_ids


def test_unbind_pack(guard: Guard) -> None:
    guard.add_rule_pack("office", OFFICE_RULES)
    guard.bind_agent("agent_x", "office")
    assert guard.unbind_agent("agent_x", "office") is True
    rule_ids = {r.rule_id for r in guard.rules_for_agent("agent_x")}
    assert "allow_office_email" not in rule_ids


def test_remove_rule_pack_clears_bindings(guard: Guard) -> None:
    guard.add_rule_pack("office", OFFICE_RULES)
    guard.bind_agent("agent_y", "office")
    assert guard.remove_rule_pack("office") is True
    assert guard.list_agent_bindings().get("agent_y", []) == []


def test_per_agent_evaluation_isolated(guard: Guard) -> None:
    guard.add_rule_pack("dev", DEV_RULES)
    guard.bind_agent("dev_001", "dev")

    from agentguard.models.events import EventType, RuntimeEvent, ToolCall

    def make_event(agent_id: str) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=EventType.TOOL_CALL_ATTEMPT,
            principal=Principal(agent_id=agent_id, session_id="s", role="basic", trust_level=1),
            tool_call=ToolCall(tool_name="shell.exec", args={"cmd": "rm -rf /"}),
        )

    decision_dev = guard.pipeline.handle_attempt(make_event("dev_001"))
    decision_other = guard.pipeline.handle_attempt(make_event("ops_001"))

    assert "deny_dev_shell" in decision_dev.matched_rules
    assert "deny_dev_shell" not in decision_other.matched_rules


def test_yaml_loader(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "office.rules").write_text(OFFICE_RULES, encoding="utf-8")
    (rules_dir / "dev.rules").write_text(DEV_RULES, encoding="utf-8")

    cfg_path = tmp_path / "rule_packs.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """\
            packs:
              office:
                sources:
                  - rules/office.rules
              dev:
                sources:
                  - rules/dev.rules
            bindings:
              agent_office_001:
                packs: [office]
              agent_dev_001:
                packs: [dev, office]
            """
        ),
        encoding="utf-8",
    )

    cfg = load_rule_pack_config(cfg_path)
    assert {p.pack_id for p in cfg.packs} == {"office", "dev"}
    assert cfg.bindings["agent_dev_001"] == ["dev", "office"]

    g = Guard(policy_source=None, builtin_rules=False, mode="enforce")
    try:
        apply_rule_pack_config(g, cfg_path)
        assert {p.pack_id for p in g.list_rule_packs()} >= {"office", "dev"}
        assert "allow_office_email" in {
            r.rule_id for r in g.rules_for_agent("agent_office_001")
        }
        assert {"deny_dev_shell", "allow_office_email"} <= {
            r.rule_id for r in g.rules_for_agent("agent_dev_001")
        }
    finally:
        g.close()
