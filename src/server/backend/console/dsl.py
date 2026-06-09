"""Bridge between the web console rule DSL and PolicyRule JSON.

The console (ported from the legacy frontend) authors rules in a small DSL:

    RULE: <name>
    ON: tool_call.<subtype>(<tool_pattern>)     # optional
    TRACE: A -> B                                # optional
    CONDITION: A.name == "tool" [AND/OR ...]
    POLICY: DENY | HUMAN_CHECK | LLM_CHECK | ALLOW | DEGRADE TO "target"
    Severity: <sev>                              # optional
    Category: <cat>                              # optional
    Reason: "<reason>"                           # optional

This module parses that DSL into PolicyRule objects (for enforcement) and
serializes PolicyRule objects back into DSL (so the console can list/edit them).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agentguard.schemas.policy import PolicyEffect, PolicyRule, RuleCondition

ACTION_TO_EFFECT = {
    "DENY": PolicyEffect.DENY,
    "HUMAN_CHECK": PolicyEffect.REQUIRE_APPROVAL,
    "LLM_CHECK": PolicyEffect.REQUIRE_REMOTE_REVIEW,
    "ALLOW": PolicyEffect.ALLOW,
    "DEGRADE": PolicyEffect.DEGRADE,
}
EFFECT_TO_ACTION = {
    PolicyEffect.DENY: "DENY",
    PolicyEffect.REQUIRE_APPROVAL: "HUMAN_CHECK",
    PolicyEffect.REQUIRE_REMOTE_REVIEW: "LLM_CHECK",
    PolicyEffect.ALLOW: "ALLOW",
    PolicyEffect.LOG_ONLY: "ALLOW",
    PolicyEffect.DEGRADE: "DEGRADE",
    PolicyEffect.SANITIZE: "DEGRADE",
}
_ON_SUBTYPE_EVENTS = {
    "requested": "tool_invoke",
    "attempted": "tool_invoke",
    "attempt": "tool_invoke",
    "completed": "tool_result",
    "result": "tool_result",
    "failed": "tool_result",
}
_PRIORITY_BY_ACTION = {
    "DENY": 90,
    "HUMAN_CHECK": 70,
    "LLM_CHECK": 60,
    "DEGRADE": 50,
    "ALLOW": 10,
}


@dataclass
class ParsedRule:
    rule: PolicyRule
    name: str
    action: str
    tool_pattern: str
    severity: str
    category: str
    reason: str
    source: str


@dataclass
class CheckReport:
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


# ---- block helpers -----------------------------------------------------
def split_blocks(source: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for raw in source.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("RULE") and current:
            blocks.append("\n".join(current).strip())
            current = []
        if line.strip() or current:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def _normalize_header(block: str) -> str:
    return re.sub(r"^RULE\s+(?!:)", "RULE: ", block, count=1, flags=re.MULTILINE)


def _named(block: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}:\s*(.+)$", block, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _action_of(policy_line: str) -> str:
    up = policy_line.strip().upper()
    for token in ("DEGRADE", "HUMAN_CHECK", "LLM_CHECK", "ALLOW", "DENY"):
        if up.startswith(token):
            return token
    return up or "DENY"


def _degrade_target(policy_line: str) -> str:
    m = re.search(r'DEGRADE\s+TO\s+"([^"]*)"', policy_line, flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _tool_pattern(block: str) -> str:
    on = _named(block, "ON")
    if on:
        m = re.search(r"\(([^)]+)\)", on)
        if m:
            return m.group(1).strip()
    cond = _named(block, "CONDITION")
    m = re.search(r'\.name\s*(?:==|CONTAINS)\s*"([^"]+)"', cond)
    if m:
        return m.group(1).strip()
    return "*"


def _on_event_types(block: str) -> list[str]:
    on = _named(block, "ON")
    m = re.search(r"tool_call\.(\w+)", on)
    if m:
        et = _ON_SUBTYPE_EVENTS.get(m.group(1).lower())
        if et:
            return [et]
    return ["tool_invoke"]


def _parse_conditions(cond_text: str) -> tuple[list[RuleCondition], list[dict[str, Any]]]:
    """Translate name-based conditions to enforceable RuleConditions.

    Other expressions are preserved verbatim for round-tripping in metadata.
    """
    enforce: list[RuleCondition] = []
    raw: list[dict[str, Any]] = []
    # Split on AND/OR while keeping it simple (treated as conjunction for enforcement).
    parts = re.split(r"\s+(?:AND|OR)\s+", cond_text)
    for part in parts:
        expr = part.strip().strip("()")
        if not expr:
            continue
        raw.append({"expr": expr})
        m = re.match(r'\S*\.name\s*(==|CONTAINS)\s*"([^"]+)"', expr)
        if m:
            op = "contains" if m.group(1).upper() == "CONTAINS" else "eq"
            enforce.append(RuleCondition(field="payload.tool_name", op=op, value=m.group(2)))
    return enforce, raw


# ---- public API --------------------------------------------------------
def parse_source(source: str) -> tuple[list[ParsedRule], CheckReport]:
    report = CheckReport()
    if not source or not source.strip():
        report.errors.append({"message": "Rule source is required."})
        return [], report

    blocks = split_blocks(source)
    if not blocks:
        report.errors.append({"message": "At least one RULE block is required."})
        return [], report

    parsed: list[ParsedRule] = []
    report.rule_count = len(blocks)
    for index, block in enumerate(blocks, start=1):
        normalized = _normalize_header(block).strip()
        lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]

        missing = [
            p.rstrip(":")
            for p in ("RULE:", "CONDITION:", "POLICY:")
            if not any(ln.startswith(p) for ln in lines)
        ]
        if missing:
            report.errors.append(
                {"message": f"Rule block {index} is missing required line(s): {', '.join(missing)}."}
            )
            continue
        if not any(ln.startswith(("ON:", "TRACE:")) for ln in lines):
            report.warnings.append(
                {"message": f"Rule block {index} has no ON/TRACE match; add one for precise targeting."}
            )

        name = _named(normalized, "RULE")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            report.errors.append({"message": f"Rule block {index}: invalid rule name '{name}'."})
            continue
        policy_line = _named(normalized, "POLICY")
        action = _action_of(policy_line)
        if action not in ACTION_TO_EFFECT:
            report.errors.append({"message": f"Rule block {index}: unsupported POLICY '{action}'."})
            continue

        tool_pattern = _tool_pattern(normalized)
        if tool_pattern == "*":
            report.warnings.append(
                {"message": f"Rule block {index} applies to all tools (no specific tool pattern)."}
            )
        severity = _named(normalized, "Severity")
        category = _named(normalized, "Category")
        reason = _unquote(_named(normalized, "Reason"))
        degrade_target = _degrade_target(policy_line)
        conditions, raw_conditions = _parse_conditions(_named(normalized, "CONDITION"))

        tool_names = [] if tool_pattern in ("", "*") else [tool_pattern]
        rule = PolicyRule(
            rule_id=name,
            effect=ACTION_TO_EFFECT[action],
            reason=reason or f"{action} for {tool_pattern}",
            priority=_PRIORITY_BY_ACTION.get(action, 50),
            event_types=_on_event_types(normalized),
            tool_names=tool_names,
            conditions=conditions,
            metadata={
                "source": "console",
                "tool_pattern": tool_pattern,
                "severity": severity,
                "category": category,
                "degrade_profile": degrade_target,
                "dsl_conditions": raw_conditions,
            },
        )
        report.hints.append({"message": f"Validated rule block {index} ('{name}')."})
        parsed.append(
            ParsedRule(
                rule=rule,
                name=name,
                action=action,
                tool_pattern=tool_pattern,
                severity=severity,
                category=category,
                reason=reason,
                source=normalized,
            )
        )
    return parsed, report


def policy_rule_to_source(rule: PolicyRule) -> str:
    """Best-effort DSL representation of a PolicyRule for console editing."""
    meta = rule.metadata or {}
    tool_pattern = meta.get("tool_pattern") or (rule.tool_names[0] if rule.tool_names else "*")
    action = EFFECT_TO_ACTION.get(rule.effect, "DENY")
    subtype = "completed" if "tool_result" in (rule.event_types or []) else "requested"

    lines = [f"RULE: {rule.rule_id}", f"ON: tool_call.{subtype}({tool_pattern})"]
    cond = _condition_source(rule, tool_pattern)
    lines.append(f"CONDITION: {cond}")
    if action == "DEGRADE":
        target = meta.get("degrade_profile") or "safe_default"
        lines.append(f'POLICY: DEGRADE TO "{target}"')
    else:
        lines.append(f"POLICY: {action}")
    if meta.get("severity"):
        lines.append(f"Severity: {meta['severity']}")
    if meta.get("category"):
        lines.append(f"Category: {meta['category']}")
    if rule.reason:
        lines.append(f'Reason: "{rule.reason}"')
    return "\n".join(lines)


def _condition_source(rule: PolicyRule, tool_pattern: str) -> str:
    raw = (rule.metadata or {}).get("dsl_conditions") or []
    exprs = [c.get("expr") for c in raw if c.get("expr")]
    if exprs:
        return " AND ".join(exprs)
    if tool_pattern and tool_pattern != "*":
        return f'A.name == "{tool_pattern}"'
    if rule.capabilities:
        return f'A.capability CONTAINS "{rule.capabilities[0]}"'
    if rule.risk_signals:
        return f'A.signal CONTAINS "{rule.risk_signals[0]}"'
    return 'A.name CONTAINS ""'


def rule_to_console_dict(
    rule: PolicyRule, *, user_managed: bool, status: str = "published"
) -> dict[str, Any]:
    meta = rule.metadata or {}
    tool_pattern = meta.get("tool_pattern") or (rule.tool_names[0] if rule.tool_names else "*")
    action = EFFECT_TO_ACTION.get(rule.effect, "DENY")
    return {
        "id": rule.rule_id,
        "name": rule.rule_id,
        "rule_id": rule.rule_id,
        "status": status,
        "tool_pattern": tool_pattern,
        "action": action,
        "version": "v1",
        "severity": meta.get("severity") or _severity_for(action),
        "category": meta.get("category") or "policy",
        "reason": rule.reason or "",
        "description": "",
        "pack_id": meta.get("pack_id") or ("console" if user_managed else "__default__"),
        "user_managed": user_managed,
        "degrade_profile": meta.get("degrade_profile") or "",
        "source": meta.get("source_text") or policy_rule_to_source(rule),
    }


def _severity_for(action: str) -> str:
    return {"DENY": "high", "HUMAN_CHECK": "high", "LLM_CHECK": "medium"}.get(action, "low")
