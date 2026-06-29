"""Compatibility parser for legacy `.rules` DSL sources."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from shared.rules.trace_pattern import parse_trace_pattern
from shared.schemas.policy import PolicyEffect, PolicyRule, RuleCondition, TraceClause

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
    "Prompt",
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


def _degrade_target(policy_line: str) -> str:
    match = re.search(r'DEGRADE\s+TO\s+"([^"]*)"', policy_line or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _on_event_types(on_line: str) -> list[str]:
    normalized = str(on_line or "").strip()
    if not normalized:
        return ["tool_invoke"]
    match = re.search(r"tool_call(?:\.(\w+))?", normalized, flags=re.IGNORECASE)
    subtype = (match.group(1) or "").lower() if match else ""
    if subtype in {"completed", "failed", "result"}:
        return ["tool_result"]
    return ["tool_invoke"]


def _tool_pattern(on_line: str) -> str:
    match = re.search(r"\(([^)]+)\)", str(on_line or ""))
    return match.group(1).strip() if match else "*"


def _compile_condition(expr: str) -> RuleCondition | None:
    parsed = re.match(
        r'^(?P<path>[A-Za-z_][A-Za-z0-9_.]*)\s+'
        r'(?P<op>NOT IN|MATCHES|CONTAINS|==|!=|>=|<=|>|<|IN)\s+'
        r'(?P<value>.+)$',
        str(expr or "").strip(),
        flags=re.IGNORECASE,
    )
    if not parsed:
        return None
    field = _condition_field(parsed.group("path"))
    if field is None:
        return None
    return RuleCondition(
        field=field,
        op=_condition_op(parsed.group("op")),
        value=_condition_value(parsed.group("value")),
    )


def _condition_field(path: str) -> str | None:
    normalized = str(path or "").strip()
    if not normalized:
        return None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\.", normalized):
        return normalized
    if normalized.startswith(("principal.", "tool.", "target.", "payload.")):
        return normalized
    return None


def _condition_op(token: str) -> str:
    normalized = str(token or "").strip().upper()
    return {
        "==": "eq",
        "!=": "ne",
        ">": "gt",
        "<": "lt",
        ">=": "gte",
        "<=": "lte",
        "IN": "in",
        "NOT IN": "not_in",
        "CONTAINS": "contains",
        "MATCHES": "regex",
    }.get(normalized, "eq")


def _condition_value(raw_value: str) -> Any:
    value = str(raw_value or "").strip()
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(item.strip()) for item in inner.split(",") if item.strip()]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return _unquote(value)


def _supports_runtime(fields: dict[str, str]) -> bool:
    trace = str(fields.get("TRACE", "")).strip()
    condition = str(fields.get("CONDITION", "")).strip()
    on_line = str(fields.get("ON", "")).strip()
    if on_line and "tool_call" not in on_line:
        return False
    unsupported_tokens = (
        "history_arg(",
        "history_result(",
        "exists_path(",
        "input.has_",
        "allowlist.",
        "denylist.",
    )
    if any(token.lower() in condition.lower() for token in unsupported_tokens):
        return False
    scrubbed = re.sub(r"\b(?:AND|OR|NOT)\b|[()]", " ", condition, flags=re.IGNORECASE)
    for part in re.split(r"\s{2,}|\n+", scrubbed):
        expr = part.strip()
        if not expr:
            continue
        if _compile_condition(expr) is None:
            return False
    return True


def _runtime_rule(fields: dict[str, str], action: str) -> PolicyRule:
    condition_text = str(fields.get("CONDITION", "")).strip()
    raw_conditions = [part.strip() for part in re.split(r"\s+AND\s+", condition_text, flags=re.IGNORECASE) if part.strip()]
    conditions = [_compile_condition(expr.strip("()")) for expr in raw_conditions]
    tool_pattern = _tool_pattern(fields.get("ON", ""))
    prompt = _unquote(fields.get("Prompt", ""))
    metadata = {
        "source": "dsl_runtime",
        "dsl_rule": fields,
        "tool_pattern": tool_pattern,
        "trace_pattern": fields.get("TRACE", ""),
        "severity": fields.get("Severity", ""),
        "category": fields.get("Category", ""),
        "degrade_profile": _degrade_target(fields.get("POLICY", "")),
        "dsl_conditions": [{"expr": expr} for expr in raw_conditions],
    }
    if action == "LLM_CHECK":
        metadata["review_kind"] = "llm_check"
        metadata["llm_prompt"] = prompt
    elif action == "HUMAN_CHECK":
        metadata["review_kind"] = "human_check"
    return PolicyRule(
        rule_id=fields["RULE"].strip(),
        effect=_ACTION_TO_EFFECT[action],
        reason=_unquote(fields.get("Reason", "")) or f"{action} for DSL rule",
        priority=_PRIORITY_BY_ACTION.get(action, 50),
        event_types=_on_event_types(fields.get("ON", "")),
        tool_names=[] if tool_pattern in ("", "*") else [tool_pattern],
        conditions=[condition for condition in conditions if condition is not None],
        condition_expr=condition_text,
        metadata=metadata,
        trace_clause=(
            TraceClause(steps=parse_trace_pattern(fields["TRACE"]))
            if fields.get("TRACE")
            else None
        ),
    )


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
        missing = [name for name in ("RULE", "POLICY") if not fields.get(name)]
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

        report.hints.append({"message": f"Validated legacy DSL block {index} ('{rule_id}')."})
        if _supports_runtime(fields):
            parsed.append(_runtime_rule(fields, action))
            continue

        report.errors.append(
            {
                "message": (
                    f"Rule block {index} ('{rule_id}') uses unsupported DSL features in the current "
                    "runtime compiler."
                )
            }
        )

    return parsed, report
