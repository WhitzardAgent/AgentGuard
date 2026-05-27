"""Build Obligations from a matched CompiledRule.

Obligation kinds produced here (consumed by ``ActionExecutor``):

  ``rewrite_tool``       ↔ legacy / DEGRADE profile
  ``mask_fields``        ↔ ``WITH REDACT(fields={"email","phone"})``
  ``require_target_in``  ↔ ``WITH REQUIRE_TARGET_IN whitelist("internal")``
  ``audit``              ↔ ``WITH AUDIT(severity="high")``   (no ToolCall rewrite)
  ``rate_limit``         ↔ ``WITH RATE_LIMIT(window="60s", max=10)``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentguard.models.decisions import Obligation
from agentguard.policy.dsl.ast import FuncCall, ObligationAST, SetLit

if TYPE_CHECKING:
    from agentguard.policy.dsl.compiler import CompiledRule
    from agentguard.models.events import RuntimeEvent


def _materialise(value: Any) -> Any:
    if isinstance(value, SetLit):
        return list(value.items)
    if isinstance(value, FuncCall):
        return {"__call__": value.name, "args": [_materialise(a) for a in value.args]}
    return value


def _obligation_from_ast(ob: ObligationAST, rule_id: str) -> Obligation | None:
    kind = ob.kind.upper()
    params = {k: _materialise(v) for k, v in ob.args.items()}
    params.setdefault("rule_id", rule_id)

    if kind == "REDACT":
        return Obligation(kind="mask_fields", params=params)
    if kind == "MASK_FIELDS":
        return Obligation(kind="mask_fields", params=params)
    if kind == "AUDIT":
        return Obligation(kind="audit", params=params)
    if kind == "REQUIRE_TARGET_IN":
        return Obligation(kind="require_target_in", params=params)
    if kind == "RATE_LIMIT":
        return Obligation(kind="rate_limit", params=params)
    # unknown obligation → pass through as opaque
    return Obligation(kind=kind.lower(), params=params)


def build_obligations(rule: "CompiledRule", event: "RuntimeEvent") -> list[Obligation]:
    """Translate a matched rule into a list of concrete obligations.

    Order matters: DEGRADE rewrites run first so later mask/audit obligations
    operate on the post-rewrite ToolCall.
    """
    out: list[Obligation] = []
    if rule.degrade_profile:
        out.append(Obligation(
            kind="rewrite_tool",
            params={"profile": rule.degrade_profile, "rule_id": rule.rule_id},
        ))
    for ob_ast in getattr(rule, "obligations_ast", []):
        o = _obligation_from_ast(ob_ast, rule.rule_id)
        if o is not None:
            out.append(o)
    # Rule-level metadata → implicit audit obligation for severity tagging.
    severity = rule.meta.get("severity") if rule.meta else None
    category = rule.meta.get("category") if rule.meta else None
    if severity or category:
        out.append(Obligation(
            kind="audit",
            params={
                "severity": severity or "medium",
                "category": category or "",
                "rule_id": rule.rule_id,
                "reason": rule.meta.get("reason", ""),
            },
        ))
    return out
