"""Compile RuleAST -> CompiledRule with a closure-based predicate.

The compiled predicate has signature:
    predicate(event: RuntimeEvent, features: dict[str, Any]) -> bool

Supports both the legacy DSL and the v2 extensions described in ``ast.py``:
  - path aliases (``caller.*``, ``tool.*``, ``event.*``, ``session.*``, ``input.*``)
  - function-style predicates (``upstream_contains_tool``, ``has_label``, …)
  - rule-level metadata (severity / category / reason / prompt / ttl_ms) carried in ``meta``
  - action-level obligations (``WITH REDACT(fields={...})`` etc.)
"""

from __future__ import annotations

import fnmatch
import functools
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from agentguard.models.errors import RuleCompileError
from agentguard.models.decisions import Action
from agentguard.models.events import RuntimeEvent
from agentguard.policy.dsl.ast import (
    Action as ActionAST,
    BareFunc,
    BoolOp,
    Compare,
    ExistsPath,
    FuncCall,
    NotOp,
    ObligationAST,
    Path,
    RuleAST,
    SetLit,
    TraceClause,
    TraceStep,
)
from agentguard.policy.dsl.parser import parse_rules
from agentguard.graph.queries import FeatureKey


Predicate = Callable[[RuntimeEvent, dict[str, Any]], bool]


@dataclass
class PathSpec:
    """Metadata for one ``exists_path(...)`` predicate inside a rule.

    Carried alongside the compiled predicate so that hot-path runtimes
    (Pipeline / SessionActor) can pre-compute the corresponding feature
    by querying the execution graph, instead of falling back to the
    label-only label-match shortcut.
    """
    feature_key: str
    source_labels: tuple[str, ...]
    max_hops: int = 6


@dataclass
class CompiledRule:
    rule_id: str
    version: str
    tool_pattern: str                 # "email.send", "shell.*", "*"
    predicate: Predicate
    action: Action
    priority: int
    degrade_profile: str | None = None
    required_features: list[str] = field(default_factory=list)
    source: str = ""
    source_block: str = ""  # the specific DSL block that produced this rule (for better frontend integration)
    event_subtype: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    obligations_ast: list[ObligationAST] = field(default_factory=list)
    path_specs: list[PathSpec] = field(default_factory=list)

    def matches_tool(self, tool_name: str) -> bool:
        if self.tool_pattern == "*":
            return True
        return fnmatch.fnmatchcase(tool_name, self.tool_pattern)

    @property
    def severity(self) -> str:
        return str(self.meta.get("severity", "medium"))

    @property
    def category(self) -> str:
        return str(self.meta.get("category", ""))

    @property
    def llm_prompt(self) -> str:
        return str(self.meta.get("prompt", "") or "")


_ACTION_MAP = {
    "DENY":        Action.DENY,
    "ALLOW":       Action.ALLOW,
    "LLM_CHECK":   Action.LLM_CHECK,
    "HUMAN_CHECK": Action.HUMAN_CHECK,   # backward-compat: direct escalation
    "DEGRADE":     Action.DEGRADE,
}


def _wrap_trace_predicate(
    trace_clause: TraceClause,
    inner: Predicate,
) -> Predicate:
    """Wrap ``inner`` with TRACE-binding logic.

    At evaluation time:
    1. Pull ``session.trace_rich`` from features and append the *current*
       tool call so that a pattern ending at the current call can match.
    2. Run ``match_with_bindings`` against the updated rich trace.
    3. If no match → return False immediately.
    4. Inject bindings into a shallow-copy of features under the key
       ``"_trace_bindings"`` and evaluate ``inner`` with this enriched dict.
    """
    from agentguard.policy.dsl.trace_pattern import match_with_bindings

    steps: list[tuple[str, str]] = [
        (s.name, s.sep) for s in trace_clause.steps
    ]

    def pred(ev: RuntimeEvent, features: dict[str, Any]) -> bool:
        trace_rich: list[dict] = list(features.get("session.trace_rich") or [])
        # Append current call so the last placeholder can match the current event.
        current_entry: dict = {
            "tool":   ev.tool_call.tool_name if ev.tool_call else "",
            "args":   dict(ev.tool_call.args or {}) if ev.tool_call else {},
            "result": None,
            "ts_ms":  ev.ts_ms,
            "label":  {},
        }
        if ev.tool_call and ev.tool_call.label:
            lb = ev.tool_call.label
            current_entry["label"] = {
                "boundary":    lb.boundary,
                "sensitivity": lb.sensitivity,
                "integrity":   lb.integrity,
            }
        trace_rich.append(current_entry)

        bindings = match_with_bindings(steps, trace_rich)
        if bindings is None:
            return False

        enriched = {**features, "_trace_bindings": bindings}
        return inner(ev, enriched)

    return pred


class RuleCompiler:
    def __init__(self, version: str = "v1") -> None:
        self.version = version

    def compile_all(self, asts: Iterable[RuleAST]) -> list[CompiledRule]:
        return [self.compile(a) for a in asts]

    def compile(self, ast: RuleAST) -> CompiledRule:
        action = _ACTION_MAP.get(ast.action.kind)
        if action is None:
            raise RuleCompileError(f"unknown action kind {ast.action.kind}")
        feats: list[str] = []
        path_specs: list[PathSpec] = []

        # Collect placeholder names so _resolve_path can recognise them.
        placeholder_names: frozenset[str] = frozenset()
        if ast.trace_clause is not None:
            placeholder_names = frozenset(s.name for s in ast.trace_clause.steps)

        predicate = self._compile_expr(
            ast.expr, ast.rule_id, feats, path_specs, placeholder_names
        )

        # Wrap with trace-binding logic when TRACE clause is present.
        if ast.trace_clause is not None:
            predicate = _wrap_trace_predicate(ast.trace_clause, predicate)

        return CompiledRule(
            rule_id=ast.rule_id,
            version=self.version,
            tool_pattern=ast.tool_pattern,
            predicate=predicate,
            action=action,
            priority=action.priority,
            degrade_profile=ast.action.profile,
            required_features=feats,
            source=ast.source,
            source_block=ast.source_block,
            event_subtype=ast.event_subtype,
            meta=dict(ast.meta),
            obligations_ast=list(ast.action.obligations),
            path_specs=path_specs,
        )

    # -------------------- expression compiler --------------------
    def _compile_expr(
        self,
        node: Any,
        rule_id: str,
        feats: list[str],
        path_specs: list[PathSpec],
        placeholder_names: frozenset[str] = frozenset(),
    ) -> Predicate:
        # v3 sentinel: TRACE clause with no CONDITION → always true.
        from agentguard.policy.dsl.parser import _TrueExpr
        if isinstance(node, _TrueExpr):
            return lambda ev, f: True

        if isinstance(node, BoolOp):
            left = self._compile_expr(node.left, rule_id, feats, path_specs, placeholder_names)
            right = self._compile_expr(node.right, rule_id, feats, path_specs, placeholder_names)
            if node.op == "AND":
                return lambda ev, f, _l=left, _r=right: _l(ev, f) and _r(ev, f)
            return lambda ev, f, _l=left, _r=right: _l(ev, f) or _r(ev, f)
        if isinstance(node, NotOp):
            inner = self._compile_expr(node.expr, rule_id, feats, path_specs, placeholder_names)
            return lambda ev, f, _i=inner: not _i(ev, f)
        if isinstance(node, Compare):
            return self._compile_compare(node, placeholder_names)
        if isinstance(node, BareFunc):
            return self._compile_bare_func(node.func)
        if isinstance(node, ExistsPath):
            key = FeatureKey.exists_path(rule_id)
            feats.append(key)
            src_labels = tuple(node.source_labels)
            max_hops = node.max_hops
            path_specs.append(PathSpec(
                feature_key=key,
                source_labels=src_labels,
                max_hops=max_hops,
            ))
            return (
                lambda ev, f, _k=key, _lbls=src_labels, _mh=max_hops:
                _exists_path_eval(ev, f, _k, _lbls, _mh)
            )
        raise RuleCompileError(f"unsupported expression node {node!r}")

    def _compile_compare(
        self,
        node: Compare,
        placeholder_names: frozenset[str] = frozenset(),
    ) -> Predicate:
        op = node.op
        left_node = node.path
        value_ast = node.value

        def resolve_left(ev: RuntimeEvent, features: dict[str, Any]) -> Any:
            if isinstance(left_node, FuncCall):
                return _call_func(left_node, ev, features)
            return _resolve_path(left_node.parts, ev, features)

        def resolve_value(ev: RuntimeEvent, features: dict[str, Any]) -> Any:
            if isinstance(value_ast, Path):
                # Bare all-caps identifier like UNFILTERED → string literal.
                if (len(value_ast.parts) == 1
                        and value_ast.parts[0].replace("-", "_").isupper()
                        and value_ast.parts[0] not in placeholder_names):
                    return value_ast.parts[0].lower()
                return _lookup_ref(value_ast.parts, ev, features)
            if isinstance(value_ast, FuncCall):
                return _call_func(value_ast, ev, features)
            if isinstance(value_ast, SetLit):
                return set(value_ast.items)
            return value_ast

        def pred(ev: RuntimeEvent, features: dict[str, Any]) -> bool:
            left = resolve_left(ev, features)
            right = resolve_value(ev, features)
            return _apply_op(op, left, right)

        return pred

    def _compile_bare_func(self, func: FuncCall) -> Predicate:
        def pred(ev: RuntimeEvent, features: dict[str, Any]) -> bool:
            result = _call_func(func, ev, features)
            return bool(result)
        return pred


# ------------------------- path aliases ---------------------------

_EVENT_TOP_FIELDS = {
    "principal", "tool_call", "scope", "goal",
    "event_type", "ts_ms", "event_id", "extra",
    "provenance_refs", "result", "trace_id",
}
# Direct attributes on ToolCall (Pydantic model). Anything *not* in this set
# but resolved under ``tool_call`` falls back to ``tool_call.args[name]``.
_TOOLCALL_SHORTCUTS = {
    "target", "args", "tool_name", "sink_type",
    "label", "syntax", "result", "authority", "ts_ms",
}

# Alias → real path rewrite applied before field lookup.  Keeps the rule
# author's surface ergonomic (``caller.role``) while re-using the existing
# Pydantic schema.
_PATH_ALIAS_REWRITES: dict[tuple[str, ...], tuple[str, ...]] = {
    # Caller = Principal
    ("caller",): ("principal",),
    # Tool  = tool_call,  tool.name → tool_call.tool_name
    ("tool", "name"): ("tool_call", "tool_name"),
    # Static labels live on tool_call.label
    ("tool", "boundary"):    ("tool_call", "label", "boundary"),
    ("tool", "sensitivity"): ("tool_call", "label", "sensitivity"),
    ("tool", "integrity"):   ("tool_call", "label", "integrity"),
    ("tool", "tags"):        ("tool_call", "label", "tags"),
    # Runtime info shortcuts
    ("tool", "result"):    ("tool_call", "result"),
    ("tool", "syntax"):    ("tool_call", "syntax"),
    ("tool", "authority"): ("tool_call", "authority"),
    ("tool", "ts_ms"):     ("tool_call", "ts_ms"),
    ("tool", "sink_type"): ("tool_call", "sink_type"),
    ("tool",): ("tool_call",),
    # Event top-level fields
    ("event", "type"): ("event_type",),
    ("event", "id"): ("event_id",),
    ("event", "timestamp"): ("ts_ms",),
    ("event", "session_id"): ("principal", "session_id"),
}


def _rewrite_alias(parts: list[str]) -> list[str]:
    # Try longer prefixes first so ("tool","boundary") wins over ("tool",).
    for prefix_len in (3, 2, 1):
        if len(parts) >= prefix_len:
            key = tuple(parts[:prefix_len])
            if key in _PATH_ALIAS_REWRITES:
                return list(_PATH_ALIAS_REWRITES[key]) + parts[prefix_len:]
    return parts


def _resolve_path(parts: list[str], ev: RuntimeEvent, features: dict[str, Any]) -> Any:
    # v3 TRACE placeholder resolution: Placeholder.field
    # Bindings injected by _wrap_trace_predicate under features["_trace_bindings"].
    trace_bindings: dict[str, dict] = features.get("_trace_bindings") or {}
    if trace_bindings and parts[0] in trace_bindings:
        entry = trace_bindings[parts[0]]
        if len(parts) == 1:
            return entry
        field = parts[1].lower()
        if field == "name":
            return entry.get("tool")
        if field in ("boundary", "sensitivity", "integrity"):
            return entry.get("label", {}).get(field)
        if field == "result":
            return entry.get("result")
        # Otherwise treat as an arg
        return entry.get("args", {}).get(parts[1])

    parts = _rewrite_alias(parts)
    top = parts[0]
    node: Any
    if top in _EVENT_TOP_FIELDS:
        node = getattr(ev, top, None)
        tail = parts[1:]
    elif top in _TOOLCALL_SHORTCUTS:
        node = getattr(ev.tool_call, top, None) if ev.tool_call is not None else None
        tail = parts[1:]
    else:
        return _lookup_ref(parts, ev, features)

    # ``tool.<param>`` shorthand: after alias-rewriting it becomes
    # ``tool_call.<param>``. If <param> is not a real ToolCall attribute,
    # treat it as a key into ``tool_call.args`` (the registered syntax dict).
    if top == "tool_call" and len(parts) >= 2:
        head = parts[1]
        if head not in _TOOLCALL_SHORTCUTS and head != "tool_name":
            tc = ev.tool_call
            if tc is not None and head in (tc.args or {}):
                node = (tc.args or {}).get(head)
                tail = parts[2:]
    for part in tail:
        node = _get_attr_or_key(node, part)
        if node is None:
            return None
    return node


def _lookup_ref(parts: list[str], ev: RuntimeEvent, features: dict[str, Any]) -> Any:
    key = ".".join(parts)
    if key in features:
        return features[key]
    # ``allowlist.X`` shorthand (legacy)
    if len(parts) == 2 and parts[0] == "allowlist":
        fb = features.get(f"allowlist.{parts[1]}")
        if fb is not None:
            return fb
    try:
        return _resolve_path(parts, ev, {})
    except Exception:
        return None


def _get_attr_or_key(node: Any, key: str) -> Any:
    if node is None:
        return None
    if hasattr(node, key):
        return getattr(node, key)
    if isinstance(node, dict):
        return node.get(key)
    return None


# ------------------------- operators ------------------------------

def _apply_op(op: str, left: Any, right: Any) -> bool:
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return _safe_lt(left, right)
    if op == "<=":
        return left == right or _safe_lt(left, right)
    if op == ">":
        return _safe_lt(right, left)
    if op == ">=":
        return left == right or _safe_lt(right, left)
    if op == "IN":
        return _in(left, right)
    if op == "NOT_IN":
        return not _in(left, right)
    if op == "MATCHES":
        return _matches(left, right)
    if op == "CONTAINS":
        return _contains(left, right)
    raise RuleCompileError(f"unsupported operator {op!r}")


def _safe_lt(a: Any, b: Any) -> bool:
    try:
        return a < b  # type: ignore[operator]
    except Exception:
        return False


def _in(needle: Any, haystack: Any) -> bool:
    if haystack is None:
        return False
    if isinstance(haystack, (set, frozenset, list, tuple)):
        return needle in haystack
    if isinstance(haystack, dict):
        return needle in haystack
    if isinstance(haystack, str):
        return needle == haystack
    return False


@functools.lru_cache(maxsize=256)
def _compile_regex(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _matches(left: Any, pattern: Any) -> bool:
    """Regex match: ``args.url MATCHES "^https://internal\\."``.

    - Right-hand side must be a string literal (Python ``re`` flavor).
    - Returns False on bad pattern, None left-hand, or non-string left-hand
      that can't be coerced.
    """
    if not isinstance(pattern, str) or left is None:
        return False
    text = left if isinstance(left, str) else str(left)
    rx = _compile_regex(pattern)
    if rx is None:
        return False
    return rx.search(text) is not None


def _contains(haystack: Any, needle: Any) -> bool:
    """Polymorphic containment used by the ``CONTAINS`` operator and the
    ``contains(x, y)`` function:

      - list / tuple / set / frozenset → element membership
      - dict                            → key membership
      - str + str needle                → substring search
      - any other / mismatched types    → False
    """
    if haystack is None:
        return False
    if isinstance(haystack, (set, frozenset, list, tuple)):
        return needle in haystack
    if isinstance(haystack, dict):
        return needle in haystack
    if isinstance(haystack, str):
        if isinstance(needle, str):
            return needle in haystack
        return False
    return False


# ------------------------- exists_path helper ---------------------

def _exists_path_eval(
    ev: RuntimeEvent,
    features: dict[str, Any],
    feature_key: str,
    source_labels: tuple[str, ...],
    max_hops: int,
) -> bool:
    """Evaluate EXISTS_PATH at hot-path time.

    Two sources of truth:
      1. A pre-computed feature (written by an async context-collector).
      2. A fallback that scans ``extra.session_labels`` — populated by the
         dispatcher's _enrich step.  This covers the common case where
         provenance is tracked via ``ProvenanceTracker.tag_resource``.
    """
    if feature_key in features:
        return bool(features[feature_key])
    labels = features.get("session.labels")
    if labels is None:
        labels = ev.extra.get("session_labels") if ev.extra else None
    if not labels:
        return False
    for pat in source_labels:
        if _label_match_any(pat, labels):
            return True
    return False


def _label_match_any(pattern: str, labels: Iterable[str]) -> bool:
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        return any(lbl == prefix or lbl.startswith(prefix + "/")
                   or lbl.startswith(prefix + ".") for lbl in labels)
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return any(lbl.startswith(prefix) for lbl in labels)
    return pattern in labels


# ------------------------- function dispatch ----------------------

def _call_func(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> Any:
    key = (func.namespace, func.name)
    handler = _FUNC_TABLE.get(key) or _FUNC_TABLE.get(("", func.name))
    if handler is None:
        return False
    try:
        return handler(func, ev, features)
    except Exception:
        return False


def _evaluate_arg(arg: Any, ev: RuntimeEvent, features: dict[str, Any]) -> Any:
    """Resolve a function-call argument to its runtime value.

    Function arguments are AST fragments produced by the parser. Literals
    (str/int/float/bool) are passed through, while ``Path`` / ``FuncCall``
    nodes are evaluated against the current event + features. ``SetLit``
    becomes a ``set``.
    """
    if isinstance(arg, Path):
        return _resolve_path(arg.parts, ev, features)
    if isinstance(arg, FuncCall):
        return _call_func(arg, ev, features)
    if isinstance(arg, SetLit):
        return set(arg.items)
    return arg


# ---- function implementations -----------------------------------

def _f_whitelist(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> set[str]:
    """``whitelist("user_known_ibans")`` — return the named allowlist as a set.

    Lookup order:
      1. ``features["allowlist.<name>"]`` (legacy)
      2. ``features[<name>]``
      3. ``ev.extra["allowlists"][<name>]``  ← session-scoped allowlist
         injected by the SDK / framework adapter

    Returns an empty set when nothing is found (so ``IN whitelist(...)``
    cleanly evaluates to False).
    """
    if not func.args:
        return set()
    name = str(func.args[0])
    val = features.get(f"allowlist.{name}") or features.get(name)
    if val is None and ev.extra:
        session_lists = ev.extra.get("allowlists")
        if isinstance(session_lists, dict):
            val = session_lists.get(name)
    if isinstance(val, (list, tuple)):
        return set(val)
    if isinstance(val, set):
        return val
    return set()


def _f_upstream_contains_tool(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    if not func.args:
        return False
    target = str(func.args[0])
    tools = features.get("session.previous_tools") \
        or (ev.extra.get("recent_tools") if ev.extra else None) or []
    return target in tools


def _f_upstream_contains_any_tool(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    wanted: set[str] = set()
    for a in func.args:
        if isinstance(a, SetLit):
            wanted |= set(a.items)
        else:
            wanted.add(str(a))
    tools = features.get("session.previous_tools") \
        or (ev.extra.get("recent_tools") if ev.extra else None) or []
    return any(t in wanted for t in tools)


def _f_derived_from_tool(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    # MVP: same as upstream_contains_tool (real provenance lives on the graph)
    return _f_upstream_contains_tool(func, ev, features)


def _f_tool_sequence_matches(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    wanted: list[str] = []
    for a in func.args:
        if isinstance(a, SetLit):
            wanted.extend(a.items)
        else:
            wanted.append(str(a))
    if not wanted:
        return False
    tools = list(features.get("session.previous_tools")
                 or (ev.extra.get("recent_tools") if ev.extra else []) or [])
    # recent_tools is stored newest-first → reverse for chronological match
    chrono = list(reversed(tools)) + [ev.tool_call.tool_name] if ev.tool_call else list(reversed(tools))
    # subsequence search
    it = iter(chrono)
    return all(any(step == x for x in it) for step in wanted)


def _f_repeated_attempts(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> int:
    tool_name = func.kwargs.get("tool") or func.kwargs.get("tool.name") or (
        func.args[0] if func.args else None
    )
    tools = features.get("session.previous_tools") \
        or (ev.extra.get("recent_tools") if ev.extra else None) or []
    current = ev.tool_call.tool_name if ev.tool_call else None
    total = sum(1 for t in tools if t == tool_name)
    if tool_name and current == tool_name:
        total += 1
    return total


def _f_distinct_targets(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> int:
    targets = features.get("session.recent_targets") or []
    return len(set(targets))


def _f_signal(signal_name: str):
    def _impl(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
        return bool(features.get(f"signal.{signal_name}", False))
    return _impl


def _f_input_has_label(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    if not func.args:
        return False
    pattern = str(func.args[0])
    labels = features.get("input.labels") or features.get("session.labels") \
        or (ev.extra.get("session_labels") if ev.extra else None) or []
    return _label_match_any(pattern, labels)


def _f_input_has_any_label(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    patterns: list[str] = []
    for a in func.args:
        if isinstance(a, SetLit):
            patterns.extend(a.items)
        else:
            patterns.append(str(a))
    labels = features.get("input.labels") or features.get("session.labels") \
        or (ev.extra.get("session_labels") if ev.extra else None) or []
    return any(_label_match_any(p, labels) for p in patterns)


def _f_caller_scope_missing(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    if not func.args:
        return False
    scope = str(func.args[0])
    scopes = set(ev.scope or [])
    extra_scopes = features.get("caller.scopes")
    if isinstance(extra_scopes, (list, tuple, set)):
        scopes |= set(extra_scopes)
    return scope not in scopes


def _f_tool_has_tag(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    if not func.args:
        return False
    tag = str(func.args[0])
    tags = features.get("tool.tags") or []
    return tag in tags


def _f_path_length(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> int:
    key = func.kwargs.get("source") or (func.args[0] if func.args else None)
    tools = list(features.get("session.previous_tools")
                 or (ev.extra.get("recent_tools") if ev.extra else []) or [])
    if key is None:
        return 0
    try:
        idx = tools.index(str(key))
    except ValueError:
        return 0
    return idx + 1  # hops from source → current call


# --- string predicates (parameter-level) -------------------------------------

def _f_starts_with(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``starts_with(args.url, "https://internal.")`` → bool."""
    if len(func.args) < 2:
        return False
    text = _evaluate_arg(func.args[0], ev, features)
    prefix = _evaluate_arg(func.args[1], ev, features)
    if not isinstance(text, str) or not isinstance(prefix, str):
        return False
    return text.startswith(prefix)


def _f_ends_with(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``ends_with(args.recipient, "@trusted.com")`` → bool."""
    if len(func.args) < 2:
        return False
    text = _evaluate_arg(func.args[0], ev, features)
    suffix = _evaluate_arg(func.args[1], ev, features)
    if not isinstance(text, str) or not isinstance(suffix, str):
        return False
    return text.endswith(suffix)


def _f_contains_func(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``contains(args.body, "click here")`` — function-form of CONTAINS."""
    if len(func.args) < 2:
        return False
    container = _evaluate_arg(func.args[0], ev, features)
    target = _evaluate_arg(func.args[1], ev, features)
    return _contains(container, target)


# --- url / email helpers -----------------------------------------------------

def _f_url_domain(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> str:
    """``url.domain(args.url)`` → lowercase hostname (``""`` if invalid)."""
    if not func.args:
        return ""
    url = _evaluate_arg(func.args[0], ev, features)
    if not isinstance(url, str) or not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    return host.lower()


def _f_url_is_external(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``url.is_external(args.url)`` → True iff the URL's host is not in
    ``allowlist.internal_domains`` (suffix-match honored).

    With no internal-domain allowlist configured, all valid URLs are
    treated as external.
    """
    if not func.args:
        return False
    url = _evaluate_arg(func.args[0], ev, features)
    if not isinstance(url, str) or not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    internal = (features.get("allowlist.internal_domains")
                or features.get("internal_domains")
                or [])
    if isinstance(internal, set):
        internal_iter: Iterable[Any] = internal
    elif isinstance(internal, (list, tuple)):
        internal_iter = internal
    else:
        internal_iter = []
    for dom in internal_iter:
        d = str(dom).lstrip(".").lower()
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return False
    return True


def _f_email_domain(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> str:
    """``email.domain(args.recipient)`` → lowercase domain part of an
    email address (``""`` if not an email)."""
    if not func.args:
        return ""
    addr = _evaluate_arg(func.args[0], ev, features)
    if not isinstance(addr, str) or "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].lower()


def _f_subset(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``subset(args.recipients, whitelist("user_address_book"))`` → True iff
    every element of the first list is present in the second collection.

    This is the "all-in" companion to the ``IN`` operator (which checks
    a single value), and is what list-valued args like
    ``send_email.recipients`` need.

    Empty first list → True (vacuous truth).
    """
    if len(func.args) < 2:
        return False
    members = _evaluate_arg(func.args[0], ev, features)
    container = _evaluate_arg(func.args[1], ev, features)
    if members is None:
        return False
    if not isinstance(members, (list, tuple, set, frozenset)):
        # Single value — treat like ``in``.
        if isinstance(container, (set, frozenset, list, tuple, dict)):
            return members in container
        return False
    if not isinstance(container, (set, frozenset, list, tuple, dict)):
        return False
    return all(m in container for m in members)


def _f_any_in(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``any_in(args.recipients, whitelist("blocked"))`` → True iff *any*
    element of the first collection is in the second. Useful for blocklists
    on list-valued parameters.
    """
    if len(func.args) < 2:
        return False
    members = _evaluate_arg(func.args[0], ev, features)
    container = _evaluate_arg(func.args[1], ev, features)
    if members is None:
        return False
    if not isinstance(members, (list, tuple, set, frozenset)):
        if isinstance(container, (set, frozenset, list, tuple, dict)):
            return members in container
        return False
    if not isinstance(container, (set, frozenset, list, tuple, dict)):
        return False
    return any(m in container for m in members)


def _f_trace(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``trace("A -> B")`` style predicate over the chronological tool-call sequence.

    Supported separators (full grammar in ``trace_pattern.py``):
        ``A -> B``           A immediately followed by B
        ``A -> * -> B``      exactly one tool call between A and B
        ``A -> ... -> B``    at least one tool call between A and B
        ``A -> ...? -> B``   B occurs anywhere after A (allows adjacent)

    The sequence inspected is ``features["session.trace_sequence"]`` (oldest-first).
    The current ``tool.name`` is appended so a pattern ending with the current call
    fires immediately on the requested phase.
    """
    from agentguard.policy.dsl.trace_pattern import (
        compile_trace_pattern,
        TracePatternError,
    )

    if not func.args:
        return False
    pattern = _evaluate_arg(func.args[0], ev, features)
    if not isinstance(pattern, str) or not pattern.strip():
        return False
    seq: list[str] = list(features.get("session.trace_sequence") or [])
    if ev.tool_call is not None:
        seq.append(ev.tool_call.tool_name)
    try:
        matcher = compile_trace_pattern(pattern)
    except TracePatternError:
        return False
    return matcher(seq)


_FUNC_TABLE: dict[tuple[str, str], Callable[[FuncCall, RuntimeEvent, dict[str, Any]], Any]] = {
    # value-returning
    ("", "whitelist"):                     _f_whitelist,
    # graph predicates
    ("", "upstream_contains_tool"):        _f_upstream_contains_tool,
    ("", "upstream_contains_any_tool"):    _f_upstream_contains_any_tool,
    ("", "derived_from_tool"):             _f_derived_from_tool,
    ("", "tool_sequence_matches"):         _f_tool_sequence_matches,
    ("", "trace"):                         _f_trace,
    ("", "path_length"):                   _f_path_length,
    # behavioural predicates
    ("", "repeated_attempts"):             _f_repeated_attempts,
    ("", "distinct_targets"):              _f_distinct_targets,
    # semantic signals
    ("", "goal_drift_detected"):           _f_signal("goal_drift"),
    ("", "scope_expansion_detected"):      _f_signal("scope_expansion"),
    ("", "suspicious_exfil_pattern"):      _f_signal("suspicious_exfil"),
    ("", "high_entropy_payload_detected"): _f_signal("high_entropy_payload"),
    ("", "goal_changed_from_initial"):     _f_signal("goal_changed"),
    # namespaced predicates
    ("input", "has_label"):                _f_input_has_label,
    ("input", "has_any_label"):            _f_input_has_any_label,
    ("caller", "scope_missing"):           _f_caller_scope_missing,
    ("tool",  "has_tag"):                  _f_tool_has_tag,
    # string predicates (parameter-level)
    ("", "starts_with"):                   _f_starts_with,
    ("", "ends_with"):                     _f_ends_with,
    ("", "contains"):                      _f_contains_func,
    # url / email helpers
    ("url",   "domain"):                   _f_url_domain,
    ("url",   "is_external"):              _f_url_is_external,
    ("email", "domain"):                   _f_email_domain,
    # list quantifiers (companions to IN / CONTAINS)
    ("", "subset"):                        _f_subset,
    ("", "any_in"):                        _f_any_in,
}
# ── Value-returning history functions (registered below after definitions) ───

def _f_history_arg(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> Any:
    """``history_arg("tool_name", "param_name")``

    Returns the value of ``param_name`` from the *last* call to ``tool_name``
    in the current session's rich trace, or ``None`` when not found.

    Example::

        WHEN history_arg("retrieve_doc", "id") == 0
    """
    if len(func.args) < 2:
        return None
    tool_name = str(_evaluate_arg(func.args[0], ev, features))
    arg_name  = str(_evaluate_arg(func.args[1], ev, features))
    trace_rich: list[dict] = features.get("session.trace_rich") or []
    for entry in reversed(trace_rich):
        if entry.get("tool") == tool_name:
            return (entry.get("args") or {}).get(arg_name)
    return None


def _f_history_result(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> Any:
    """``history_result("tool_name")``

    Returns the return-value from the *last* call to ``tool_name`` in the
    current session's rich trace, or ``None`` when not found / not yet
    available.

    Example::

        WHEN history_result("classify_doc") == "confidential"
             AND tool.addr != "admin@example.com"
    """
    if not func.args:
        return None
    tool_name = str(_evaluate_arg(func.args[0], ev, features))
    trace_rich: list[dict] = features.get("session.trace_rich") or []
    for entry in reversed(trace_rich):
        if entry.get("tool") == tool_name:
            return entry.get("result")
    return None


def _f_history_args_match(func: FuncCall, ev: RuntimeEvent, features: dict[str, Any]) -> bool:
    """``history_args_match("tool_name", "param", value)``

    Convenience boolean predicate — equivalent to
    ``history_arg("tool_name", "param") == value`` but usable as a
    standalone condition without extra syntax.

    Example::

        WHEN history_args_match("retrieve_doc", "id", 0)
    """
    if len(func.args) < 3:
        return False
    tool_name = str(_evaluate_arg(func.args[0], ev, features))
    arg_name  = str(_evaluate_arg(func.args[1], ev, features))
    expected  = _evaluate_arg(func.args[2], ev, features)
    trace_rich: list[dict] = features.get("session.trace_rich") or []
    for entry in reversed(trace_rich):
        if entry.get("tool") == tool_name:
            actual = (entry.get("args") or {}).get(arg_name)
            return actual == expected
    return False


def compile_rules(*sources: str, version: str = "v1") -> list[CompiledRule]:
    asts = parse_rules(*sources)
    return RuleCompiler(version=version).compile_all(asts)


# ── Late registration of value-returning functions ────────────────────────
# These functions are defined after _FUNC_TABLE to keep the dict readable;
# register them here so module-level order doesn't cause NameErrors.
_FUNC_TABLE.update({
    ("", "history_arg"):        _f_history_arg,
    ("", "history_result"):     _f_history_result,
    ("", "history_args_match"): _f_history_args_match,
})
