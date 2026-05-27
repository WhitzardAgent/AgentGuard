"""AST node definitions for the AgentGuard rule DSL.

Three syntax styles are supported — all compile to the same ``RuleAST``.

Legacy (v1)::

    RULE r1 ON tool_call(email.send) IF ... THEN DENY

Chain-defence (v2)::

    RULE r1
    ON tool_call.requested
    WHEN
      tool.name IN {"send_email"} AND exists_path(...)
    THEN DENY
    WITH severity = "high", category = "data_exfiltration"

Declarative trace (v3) — new, human-readable::

    RULE: code_execution
    TRACE: Src -> ... -> Dst
    CONDITION: Src.integrity == "unfiltered" AND Dst.name == "ExecuteCode"
    POLICY: LLM_CHECK
    Prompt: "Apply a strict code-execution review policy."
    Severity: critical
    Category: injection
    Reason: unfiltered data reaching code executor

In v3, ``TRACE`` names placeholder variables (``Src``, ``Dst``, ``Mid``…)
that are bound to matching trace entries at evaluation time.  ``CONDITION``
can then reference those placeholders by name:

    Placeholder.name          →  tool_name of the matched call
    Placeholder.integrity     →  label.integrity of the matched call
    Placeholder.sensitivity   →  label.sensitivity
    Placeholder.boundary      →  label.boundary
    Placeholder.result        →  return value of the matched call
    Placeholder.<param>       →  args[param] of the matched call

Multiple placeholders are supported::

    TRACE: A -> ... -> B -> * -> C
    CONDITION: A.sensitivity == "high"
               AND C.name == "http.post"
               AND B.boundary != "privileged"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Path:
    parts: list[str]

    def __str__(self) -> str:
        return ".".join(self.parts)


@dataclass
class SetLit:
    items: list[str]


@dataclass
class FuncCall:
    """Function-call node used for predicates and value lookups.

    Examples
    --------
    ``upstream_contains_tool("read_secrets")``      →  name="upstream_contains_tool"
    ``caller.scope_missing("x")``                   →  namespace="caller", name="scope_missing"
    ``input.has_any_label({"pii/*", "hr/*"})``      →  namespace="input",  name="has_any_label"
    ``whitelist("approved_targets")``               →  name="whitelist"  (value-returning)
    ``repeated_attempts(tool="x", window="5m")``    →  kwargs carry the keyword args
    """
    name: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    namespace: str = ""


@dataclass
class Compare:
    path: Any          # Path | FuncCall
    op: str            # ==, !=, <, <=, >, >=, IN, NOT_IN
    value: Any         # literal | SetLit | Path | FuncCall


@dataclass
class BareFunc:
    """A function call used *standalone* as a predicate (returns bool)."""
    func: FuncCall


@dataclass
class ExistsPath:
    source_labels: list[str]
    max_hops: int = 6
    sink: str = "current_call"
    over: str = "execution_graph"


@dataclass
class BoolOp:
    op: str            # AND | OR
    left: Any
    right: Any


@dataclass
class NotOp:
    expr: Any


@dataclass
class ObligationAST:
    """Action-level obligation attached via ``WITH <OBLIGATION>(...)``."""
    kind: str                          # REDACT | AUDIT | REQUIRE_TARGET_IN | MASK_FIELDS
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Action:
    kind: str                          # DENY | ALLOW | HUMAN_CHECK | LLM_CHECK | DEGRADE
    profile: str | None = None         # degrade profile name or target tool
    obligations: list[ObligationAST] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# v3 TRACE clause  (named-placeholder trace binding)
# ─────────────────────────────────────────────────────────────────────────────

#: Separator types that mirror the existing trace_pattern primitives.
#:   ""        → adjacent  (A -> B)
#:   "->"      → adjacent  (explicit)
#:   "-> *"    → exactly one hop between
#:   "-> ..."  → at-least-one hop between
#:   "-> ...?" → anywhere after (zero or more)
TraceStepSep = str


@dataclass
class TraceStep:
    """One named placeholder in a v3 TRACE clause.

    ``name`` is the variable name used in CONDITION (e.g. ``Src``, ``Tool-A``).
    ``sep`` is the separator *leading into* this step (empty for the first).
    """
    name: str
    sep: TraceStepSep = ""


@dataclass
class TraceClause:
    """Parsed TRACE clause: an ordered list of named placeholder steps.

    Example::

        TRACE: Src -> ... -> Mid -> * -> Dst

    compiles to::

        TraceClause(steps=[
            TraceStep("Src",  ""),
            TraceStep("Mid",  "-> ..."),
            TraceStep("Dst",  "-> *"),
        ])
    """
    steps: list[TraceStep]


@dataclass
class RuleAST:
    rule_id: str
    tool_pattern: str
    expr: Any
    action: Action
    event_subtype: str = ""            # "", "requested", "completed", ...
    source: str = ""
    source_block: str = ""
    meta: dict[str, Any] = field(default_factory=dict)   # severity/category/reason/prompt/ttl_ms
    trace_clause: TraceClause | None = None   # v3 TRACE bindings (None → no binding)
