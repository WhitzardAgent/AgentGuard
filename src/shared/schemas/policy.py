"""Policy rule schema, condition matching and effect mapping."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shared.rules.trace_pattern import TraceStep, match_with_bindings
from shared.schemas.decisions import DecisionType
from shared.schemas.events import RuntimeEvent


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    SANITIZE = "sanitize"
    DEGRADE = "degrade"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_REMOTE_REVIEW = "require_remote_review"
    LOG_ONLY = "log_only"


_EFFECT_TO_DECISION = {
    PolicyEffect.ALLOW: DecisionType.ALLOW,
    PolicyEffect.DENY: DecisionType.DENY,
    PolicyEffect.SANITIZE: DecisionType.SANITIZE,
    PolicyEffect.DEGRADE: DecisionType.DEGRADE,
    PolicyEffect.REQUIRE_APPROVAL: DecisionType.REQUIRE_APPROVAL,
    PolicyEffect.REQUIRE_REMOTE_REVIEW: DecisionType.REQUIRE_REMOTE_REVIEW,
    PolicyEffect.LOG_ONLY: DecisionType.LOG_ONLY,
}


def effect_to_decision(effect: PolicyEffect) -> DecisionType:
    return _EFFECT_TO_DECISION[effect]


@dataclass
class TraceClause:
    steps: list[TraceStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [step.to_dict() for step in self.steps]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceClause":
        return cls(
            steps=[TraceStep.from_dict(item) for item in data.get("steps") or []],
        )


@dataclass
class RuleCondition:
    """A single field predicate. `field` is a dotted path into the event dict.

    Special prefixes:
      trace.contains_event_type / trace.contains_signal -> trace-window predicates
    """

    field: str
    op: str = "eq"
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleCondition":
        return cls(field=data["field"], op=data.get("op", "eq"), value=data.get("value"))


def _resolve(path: str, root: dict[str, Any]) -> Any:
    parts = path.split(".")
    bindings = root.get("_trace_bindings")
    if isinstance(bindings, dict) and parts and parts[0] in bindings:
        return _resolve_trace_binding(bindings[parts[0]], parts[1:])
    cur: Any = root
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _resolve_trace_binding(binding: dict[str, Any], parts: list[str]) -> Any:
    if not parts:
        return binding
    head = parts[0]
    if head == "name":
        return binding.get("tool_name")
    if head in {"boundary", "sensitivity", "integrity"}:
        return (binding.get("labels") or {}).get(head)
    if head == "result":
        return binding.get("result")
    return (binding.get("arguments") or {}).get(head)


def _apply_op(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gte":
        try:
            return float(actual) >= float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lte":
        try:
            return float(actual) <= float(expected)
        except (TypeError, ValueError):
            return False
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op == "contains":
        return expected in actual if actual is not None else False
    if op == "icontains":
        return str(expected).lower() in str(actual or "").lower()
    if op == "any_in":
        a = set(actual or []) if isinstance(actual, (list, set, tuple)) else {actual}
        return bool(a & set(expected or []))
    if op == "regex":
        return bool(re.search(str(expected), str(actual or "")))
    if op == "exists":
        return (actual is not None) == bool(expected)
    if op == "gt":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lt":
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    return False


@dataclass
class PolicyRule:
    rule_id: str
    effect: PolicyEffect
    agent_id: str | None = None
    reason: str = ""
    priority: int = 0
    event_types: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    conditions: list[RuleCondition] = field(default_factory=list)
    condition_expr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_clause: TraceClause | None = None

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "effect": self.effect.value,
            "agent_id": self.agent_id,
            "reason": self.reason,
            "priority": self.priority,
            "event_types": list(self.event_types),
            "tool_names": list(self.tool_names),
            "capabilities": list(self.capabilities),
            "risk_signals": list(self.risk_signals),
            "conditions": [c.to_dict() for c in self.conditions],
            "condition_expr": self.condition_expr,
            "metadata": self.metadata,
            "trace_clause": self.trace_clause.to_dict() if self.trace_clause else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyRule":
        return cls(
            rule_id=data["rule_id"],
            effect=PolicyEffect(data["effect"]),
            agent_id=(
                str(data.get("agent_id")).strip()
                if data.get("agent_id") not in (None, "")
                else None
            ),
            reason=data.get("reason", ""),
            priority=int(data.get("priority", 0)),
            event_types=list(data.get("event_types") or []),
            tool_names=list(data.get("tool_names") or []),
            capabilities=list(data.get("capabilities") or []),
            risk_signals=list(data.get("risk_signals") or []),
            conditions=[RuleCondition.from_dict(c) for c in data.get("conditions") or []],
            condition_expr=str(data.get("condition_expr") or ""),
            metadata=dict(data.get("metadata") or {}),
            trace_clause=(
                TraceClause.from_dict(data["trace_clause"])
                if data.get("trace_clause")
                else None
            ),
        )

    # ---- matching ------------------------------------------------------
    def matches(
        self,
        event: RuntimeEvent,
        trace_window: list[RuntimeEvent] | None = None,
    ) -> bool:
        if self.agent_id not in (None, ""):
            event_agent_id = str(event.context.agent_id or "").strip()
            if event_agent_id != str(self.agent_id).strip():
                return False

        if self.event_types and event.event_type.value not in self.event_types:
            return False

        if self.tool_names:
            tool = getattr(event.payload, "tool_name", None)
            if not _wildcard_match(tool, self.tool_names):
                return False

        if self.capabilities:
            caps = set(getattr(event.payload, "capabilities", []) or [])
            if not (caps & set(self.capabilities)):
                return False

        if self.risk_signals:
            if not (set(event.risk_signals) & set(self.risk_signals)):
                return False

        event_dict = event.to_dict()
        principal = _principal_view(event)
        tool = _tool_view(event)
        target = _target_view(tool)
        trace_bindings = _trace_bindings(self.trace_clause, event, trace_window or [])
        if self.trace_clause is not None and trace_bindings is None:
            return False
        match_root = {
            **event_dict,
            "principal": principal,
            "tool": tool,
            "target": target,
            "_trace_bindings": trace_bindings or {},
        }
        if self.condition_expr.strip():
            if not _evaluate_condition_expr(self.condition_expr, match_root, trace_window or []):
                return False
        for cond in self.conditions:
            if cond.field.startswith("trace."):
                if not _match_trace(cond, trace_window or []):
                    return False
                continue
            actual = _resolve(cond.field, match_root)
            if not _apply_op(cond.op, actual, cond.value):
                return False
        return True


def _principal_view(event: RuntimeEvent) -> dict[str, Any]:
    context = event.context
    metadata_principal = {}
    if isinstance(event.metadata, dict):
        metadata_principal = dict(event.metadata.get("principal") or {})
    context_principal = {}
    if isinstance(context.metadata, dict):
        context_principal = dict(context.metadata.get("principal") or {})
    principal = {
        **context_principal,
        **metadata_principal,
        "agent_id": context.agent_id,
        "user_id": context.user_id,
        "session_id": context.session_id,
    }
    if "role" not in principal and isinstance(context.metadata, dict):
        principal["role"] = context.metadata.get("role")
    if "trust_level" not in principal and isinstance(context.metadata, dict):
        principal["trust_level"] = context.metadata.get("trust_level")
    return principal


def _tool_view(event: RuntimeEvent) -> dict[str, Any]:
    payload = event.payload.to_dict()
    tool: dict[str, Any] = {}
    tool_name = payload.get("tool_name")
    if tool_name is not None:
        tool["name"] = tool_name
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        tool.update(arguments)
    result = payload.get("result")
    if result is not None:
        tool["result"] = result
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        tool["capabilities"] = list(capabilities)
    labels = {}
    if isinstance(event.metadata, dict):
        labels = dict(event.metadata.get("labels") or event.metadata.get("tool_labels") or {})
    for key in ("boundary", "sensitivity", "integrity"):
        if key in labels and labels.get(key) not in (None, ""):
            tool[key] = labels.get(key)
    return tool


def _target_view(tool: dict[str, Any]) -> dict[str, Any]:
    url = tool.get("url") or tool.get("uri") or tool.get("endpoint")
    recipient = tool.get("to") or tool.get("addr") or tool.get("email")
    raw = url or recipient
    domain = _extract_domain(str(raw)) if raw not in (None, "") else None
    return {
        "url": url,
        "domain": domain,
        "raw": raw,
    }


def _trace_bindings(
    clause: TraceClause | None,
    event: RuntimeEvent,
    window: list[RuntimeEvent],
) -> dict[str, dict[str, Any]] | None:
    if clause is None:
        return {}
    if not clause.steps:
        return None
    entries = [_trace_entry(item) for item in window if _is_tool_event(item)]
    if _is_tool_event(event):
        entries.append(_trace_entry(event))
    return match_with_bindings(clause.steps, entries)


def _is_tool_event(event: RuntimeEvent) -> bool:
    return event.event_type.value == "tool_invoke"


def _trace_entry(event: RuntimeEvent) -> dict[str, Any]:
    payload = event.payload.to_dict()
    labels = {}
    if isinstance(event.metadata, dict):
        labels = dict(event.metadata.get("labels") or event.metadata.get("tool_labels") or {})
    return {
        "tool_name": payload.get("tool_name"),
        "arguments": dict(payload.get("arguments") or {}),
        "result": payload.get("result"),
        "labels": labels,
    }


def _extract_domain(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if "@" in text and "://" not in text:
        return text.rsplit("@", 1)[-1].lower()
    match = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://([^/:?#]+)", text)
    if match:
        return match.group(1).lower()
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", text):
        return text.lower()
    return None


def _wildcard_match(value: Any, patterns: list[str]) -> bool:
    if value is None:
        return False
    for p in patterns:
        if p == "*" or p == value:
            return True
        if p.endswith("*") and str(value).startswith(p[:-1]):
            return True
    return False


def _evaluate_condition_expr(
    expr: str,
    match_root: dict[str, Any],
    trace_window: list[RuntimeEvent],
) -> bool:
    tokens = _tokenize_condition_expr(expr)
    if not tokens:
        return True
    parsed, index = _parse_or(tokens, 0)
    if parsed is None or index != len(tokens):
        return False
    return _eval_condition_node(parsed, match_root, trace_window)


def _tokenize_condition_expr(expr: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_quote = False
    quote_char = ""
    brace_depth = 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if in_quote:
            current.append(ch)
            if ch == "\\" and i + 1 < len(expr):
                i += 1
                current.append(expr[i])
            elif ch == quote_char:
                in_quote = False
            i += 1
            continue
        if ch in {'"', "'"}:
            in_quote = True
            quote_char = ch
            current.append(ch)
            i += 1
            continue
        if ch == "{":
            brace_depth += 1
            current.append(ch)
            i += 1
            continue
        if ch == "}":
            brace_depth = max(0, brace_depth - 1)
            current.append(ch)
            i += 1
            continue
        if brace_depth == 0 and ch in "()":
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            tokens.append(ch)
            current = []
            i += 1
            continue
        if brace_depth == 0 and ch.isspace():
            token = "".join(current).strip()
            if token:
                upper = token.upper()
                if upper in {"AND", "OR", "NOT"}:
                    tokens.append(upper)
                else:
                    tokens.append(token)
                current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    token = "".join(current).strip()
    if token:
        upper = token.upper()
        tokens.append(upper if upper in {"AND", "OR", "NOT"} else token)
    return _merge_condition_atoms(tokens)


def _merge_condition_atoms(tokens: list[str]) -> list[str]:
    merged: list[str] = []
    operators = {"AND", "OR", "NOT", "(", ")"}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"(", ")"}:
            merged.append(token)
            i += 1
            continue
        if token in {"AND", "OR", "NOT"}:
            merged.append(token)
            i += 1
            continue
        parts = [token]
        j = i + 1
        while j < len(tokens) and tokens[j] not in operators:
            parts.append(tokens[j])
            j += 1
        merged.append(" ".join(parts).strip())
        i = j
    return merged


def _parse_or(tokens: list[str], index: int) -> tuple[Any, int]:
    left, index = _parse_and(tokens, index)
    if left is None:
        return None, index
    while index < len(tokens) and tokens[index] == "OR":
        right, next_index = _parse_and(tokens, index + 1)
        if right is None:
            return None, index
        left = ("or", left, right)
        index = next_index
    return left, index


def _parse_and(tokens: list[str], index: int) -> tuple[Any, int]:
    left, index = _parse_not(tokens, index)
    if left is None:
        return None, index
    while index < len(tokens) and tokens[index] == "AND":
        right, next_index = _parse_not(tokens, index + 1)
        if right is None:
            return None, index
        left = ("and", left, right)
        index = next_index
    return left, index


def _parse_not(tokens: list[str], index: int) -> tuple[Any, int]:
    if index < len(tokens) and tokens[index] == "NOT":
        node, next_index = _parse_not(tokens, index + 1)
        if node is None:
            return None, index
        return ("not", node), next_index
    return _parse_primary(tokens, index)


def _parse_primary(tokens: list[str], index: int) -> tuple[Any, int]:
    if index >= len(tokens):
        return None, index
    token = tokens[index]
    if token == "(":
        node, next_index = _parse_or(tokens, index + 1)
        if node is None or next_index >= len(tokens) or tokens[next_index] != ")":
            return None, index
        return node, next_index + 1
    if token == ")":
        return None, index
    return ("atom", token), index + 1


def _eval_condition_node(
    node: Any,
    match_root: dict[str, Any],
    trace_window: list[RuntimeEvent],
) -> bool:
    kind = node[0]
    if kind == "atom":
        cond = _parse_expr_atom(node[1])
        if cond is None:
            return False
        if cond.field.startswith("trace."):
            return _match_trace(cond, trace_window)
        return _apply_op(cond.op, _resolve(cond.field, match_root), cond.value)
    if kind == "not":
        return not _eval_condition_node(node[1], match_root, trace_window)
    if kind == "and":
        return _eval_condition_node(node[1], match_root, trace_window) and _eval_condition_node(node[2], match_root, trace_window)
    if kind == "or":
        return _eval_condition_node(node[1], match_root, trace_window) or _eval_condition_node(node[2], match_root, trace_window)
    return False


def _parse_expr_atom(expr: str) -> RuleCondition | None:
    parsed = re.match(
        r'^(?P<path>[A-Za-z_][A-Za-z0-9_.]*)\s+'
        r'(?P<op>NOT IN|MATCHES|CONTAINS|==|!=|>=|<=|>|<|IN)\s+'
        r'(?P<value>.+)$',
        str(expr or "").strip(),
        flags=re.IGNORECASE,
    )
    if not parsed:
        return None
    return RuleCondition(
        field=parsed.group("path").strip(),
        op=_normalize_expr_op(parsed.group("op")),
        value=_parse_expr_value(parsed.group("value")),
    )


def _normalize_expr_op(token: str) -> str:
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


def _parse_expr_value(raw_value: str) -> Any:
    value = str(raw_value or "").strip()
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote_expr_value(item.strip()) for item in inner.split(",") if item.strip()]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return _unquote_expr_value(value)


def _unquote_expr_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _match_trace(cond: RuleCondition, window: list[RuntimeEvent]) -> bool:
    key = cond.field.split(".", 1)[1]
    if key == "contains_event_type":
        return any(e.event_type.value == cond.value for e in window)
    if key == "contains_signal":
        return any(cond.value in e.risk_signals for e in window)
    if key == "sequence":
        # value is an ordered list of event_type strings to appear in order.
        wanted = list(cond.value or [])
        idx = 0
        for e in window:
            if idx < len(wanted) and e.event_type.value == wanted[idx]:
                idx += 1
        return idx >= len(wanted)
    return False
