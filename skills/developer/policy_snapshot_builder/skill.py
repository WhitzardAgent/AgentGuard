"""PolicySnapshotBuilderSkill: compile rules into a snapshot with indexes."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.policy import PolicyRule
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from skills.base import BaseSkill, SkillInput, SkillOutput


class PolicySnapshotBuilderSkill(BaseSkill):
    name = "policy_snapshot_builder"
    description = "Compile rules into a versioned policy snapshot with indexes."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        data = input.data or {}
        raw_rules = data.get("rules") or []
        version = data.get("version") or "v1"
        try:
            rules = [PolicyRule.from_dict(r) for r in raw_rules]
        except (KeyError, ValueError) as exc:
            return SkillOutput(False, {}, explanation=f"invalid rule: {exc}")

        snapshot = PolicySnapshot(version=version, rules=rules)
        indexes = {
            "capability_index": _index_keys(snapshot._by_capability),
            "risk_label_index": _index_keys(snapshot._by_risk),
            "event_type_index": _index_keys(snapshot._by_event),
        }
        return SkillOutput(
            True,
            {
                "snapshot": snapshot.to_dict(),
                "indexes": indexes,
                "stable_hash": snapshot.stable_hash(),
                "rule_count": len(rules),
            },
            explanation=f"compiled {len(rules)} rules into snapshot {version}",
        )


def _index_keys(index: dict[str, list]) -> dict[str, Any]:
    return {k: [r.rule_id for r in v] for k, v in index.items()}
