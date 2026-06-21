"""Compatibility parser for legacy `.rules` DSL sources.

This parser is intentionally conservative: it validates and loads rule blocks,
but compiles them into metadata-only `PolicyRule` objects that never match the
current runtime engine. The goal is to support README / CLI compatibility and
safe ingestion of legacy DSL files such as `rules/v3_trace_demo.rules` without
miscompiling complex TRACE semantics into incorrect runtime enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from shared.schemas.policy import PolicyEffect, PolicyRule, RuleCondition

_ACTION_TO_EFFECT = {
    "DENY": PolicyEffect.DENY,
    "HUMAN_CHECK": PolicyEffect.REQUIRE_APPROVAL,
    "LLM_CHECK": PolicyEffect.REQUIRE_REMOTE_REVIEW,
    "ALLOW": PolicyEffect.ALLOW,
    "DEGRADE": PolicyEffect.DEGRADE,
}

_PRIORITY_BY_ACTION = {
    "DENY": 90,
    "HUMAN_CHECK": 70,
    "LLM_CHECK": 60,
    "DEGRADE": 50,
    "ALLOW": 10,
}

_HEADER_NAMES = (
    "RULE",
    "ON",
    "TRACE",
    "CONDITION",
    "POLICY",
    "Severity",
    "Category",
    "Reason",
)


@dataclass
class DSLCompatReport:
    rule_count: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    hints: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rule_count": self.rule_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "hints": self.hints,
            "source_file": "",
        }


def split_rule_blocks(source: str) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    in_block = False
    for raw in source.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            if in_block:
                current.append("")
            continue
        if stripped.startswith("#") and not in_block:
            continue
        if re.match(r"^RULE\s*:?.+", stripped):
            if current:
                blocks.append(current)
            current = [stripped if stripped.startswith("RULE:") else re.sub(r"^RULE\s+", "RULE: ", stripped, count=1)]
            in_block = True
            continue
        if in_block:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).strip() for block in blocks if any(line.strip() for line in block)]


def _parse_block(block: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current_label: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        header_match = re.match(r"^([A-Za-z][A-Za-z_ ]*):\s*(.*)$", stripped)
        if header_match and header_match.group(1) in _HEADER_NAMES:
            current_label = header_match.group(1)
            fields.setdefault(current_label, []).append(header_match.group(2).strip())
            continue
        if current_label in {"CONDITION", "TRACE", "ON", "POLICY"}:
            fields.setdefault(current_label, []).append(stripped)
    return {key: "\n".join([item for item in values if item]).strip() for key, values in fields.items()}


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _action_of(policy_line: str) -> str:
    normalized = (policy_line or "").strip().upper()
    for token in ("DEGRADE", "HUMAN_CHECK", "LLM_CHECK", "ALLOW", "DENY"):
        if normalized.startswith(token):
            return token
    return normalized or "DENY"


def parse_legacy_rules(source: str) -> tuple[list[PolicyRule], DSLCompatReport]:
    report = DSLCompatReport()
    if not source or not source.strip():
        report.errors.append({"message": "Rule source is required."})
        return [], report

    blocks = split_rule_blocks(source)
    report.rule_count = len(blocks)
    if not blocks:
        report.errors.append({"message": "At least one RULE block is required."})
        return [], report

    parsed: list[PolicyRule] = []
    for index, block in enumerate(blocks, start=1):
        fields = _parse_block(block)
        missing = [name for name in ("RULE", "CONDITION", "POLICY") if not fields.get(name)]
        if missing:
            report.errors.append(
                {"message": f"Rule block {index} is missing required line(s): {', '.join(missing)}."}
            )
            continue

        rule_id = fields["RULE"].strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", rule_id):
            report.errors.append({"message": f"Rule block {index}: invalid rule name '{rule_id}'."})
            continue

        action = _action_of(fields.get("POLICY", ""))
        effect = _ACTION_TO_EFFECT.get(action)
        if effect is None:
            report.errors.append({"message": f"Rule block {index}: unsupported POLICY '{action}'."})
            continue

        if not fields.get("ON") and not fields.get("TRACE"):
            report.warnings.append(
                {"message": f"Rule block {index} has no ON/TRACE match; add one for precise targeting."}
            )

        report.warnings.append(
            {
                "message": (
                    f"Rule block {index} ('{rule_id}') was parsed in compatibility mode; "
                    "complex DSL semantics are stored as metadata only."
                )
            }
        )
        report.hints.append({"message": f"Validated legacy DSL block {index} ('{rule_id}')."})

        parsed.append(
            PolicyRule(
                rule_id=rule_id,
                effect=effect,
                reason=_unquote(fields.get("Reason", "")) or f"{action} for legacy DSL rule",
                priority=_PRIORITY_BY_ACTION.get(action, 50),
                event_types=["tool_invoke"],
                conditions=[
                    RuleCondition(
                        field="metadata.__dsl_compat_runtime_enabled__",
                        op="eq",
                        value=True,
                    )
                ],
                metadata={
                    "source": "legacy_dsl_compat",
                    "dsl_rule": block,
                    "dsl_on": fields.get("ON", ""),
                    "dsl_trace": fields.get("TRACE", ""),
                    "dsl_condition": fields.get("CONDITION", ""),
                    "dsl_policy": fields.get("POLICY", ""),
                    "severity": fields.get("Severity", ""),
                    "category": fields.get("Category", ""),
                    "parsed_only": True,
                },
            )
        )

    return parsed, report

