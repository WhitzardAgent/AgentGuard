"""AgentGuard DSL rule validator with rich, actionable diagnostics.

Invoked via::

    python -m agentguard check rules/my_policy.rules
    python -m agentguard check --stdin          # pipe rule text from stdin
    python -m agentguard check --json rules/    # JSON output for tooling

Or as a library::

    from agentguard.policy.dsl.validator import validate_source
    report = validate_source(src)
    print(report.summary())
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Knowledge tables used by semantic validators
# ──────────────────────────────────────────────────────────────────────────────

VALID_BOUNDARIES   = {"internal", "external", "privileged"}
VALID_SENSITIVITIES = {"low", "moderate", "high"}
VALID_INTEGRITIES  = {"trusted", "unfiltered"}

# All path aliases the resolver understands
_KNOWN_PREFIXES = {
    "tool", "caller", "principal", "target", "input", "event",
    "session", "allowlist",
}

# All built-in predicate functions
_KNOWN_FUNCS = {
    "trace", "exists_path", "upstream_contains_tool", "upstream_contains_any_tool",
    "derived_from_tool", "tool_sequence_matches",
    "goal_drift_detected", "scope_expansion_detected",
    "suspicious_exfil_pattern", "high_entropy_payload_detected",
    "goal_changed_from_initial", "repeated_attempts",
    "whitelist",
    "history_arg", "history_result", "history_args_match",
}

# Known label sub-fields
_KNOWN_LABEL_FIELDS = {"boundary", "sensitivity", "integrity", "tags"}

# Known tool alias sub-fields (non-label)
_KNOWN_TOOL_FIELDS = {"name", "result", "syntax", "authority", "ts_ms", "sink_type"} | _KNOWN_LABEL_FIELDS

# Valid actions
_VALID_ACTIONS = {"DENY", "ALLOW", "HUMAN_CHECK", "LLM_CHECK", "DEGRADE"}

# Known v3 metadata keys
_V3_META_KEYS = {"severity", "category", "reason", "prompt", "priority", "ttl_ms"}

# Severity values
_VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}

# ──────────────────────────────────────────────────────────────────────────────
# Diagnostic dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Diagnostic:
    level: str           # "error" | "warning" | "hint"
    rule_id: str | None
    message: str
    suggestion: str = ""
    line: int | None = None

    def __str__(self) -> str:
        loc = f"(line {self.line}) " if self.line else ""
        rule = f"[{self.rule_id}] " if self.rule_id else ""
        tag = {"error": "✗ ERROR", "warning": "⚠ WARN ", "hint": "ℹ HINT "}.get(self.level, self.level)
        lines = [f"{tag}  {rule}{loc}{self.message}"]
        if self.suggestion:
            for s_line in textwrap.wrap(self.suggestion, 90, initial_indent="         → ", subsequent_indent="           "):
                lines.append(s_line)
        return "\n".join(lines)


@dataclass
class ValidationReport:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    rule_count: int = 0
    source_file: str = ""

    def errors(self)   -> list[Diagnostic]: return [d for d in self.diagnostics if d.level == "error"]
    def warnings(self) -> list[Diagnostic]: return [d for d in self.diagnostics if d.level == "warning"]
    def hints(self)    -> list[Diagnostic]: return [d for d in self.diagnostics if d.level == "hint"]

    @property
    def ok(self) -> bool:
        return len(self.errors()) == 0

    def summary(self, *, color: bool = True) -> str:
        RED   = "\033[31m" if color else ""
        YEL   = "\033[33m" if color else ""
        GRN   = "\033[32m" if color else ""
        CYAN  = "\033[36m" if color else ""
        RESET = "\033[0m" if color else ""

        lines: list[str] = []
        src = f"  {self.source_file}" if self.source_file else ""
        lines.append(f"{CYAN}AgentGuard Rule Validator{src}{RESET}")
        lines.append("")

        if not self.diagnostics:
            lines.append(f"{GRN}✓ {self.rule_count} rules — all checks passed{RESET}")
            return "\n".join(lines)

        # Group by rule
        by_rule: dict[str | None, list[Diagnostic]] = {}
        for d in self.diagnostics:
            by_rule.setdefault(d.rule_id, []).append(d)

        for rule_id, diags in by_rule.items():
            label = f"[{rule_id}]" if rule_id else "[file-level]"
            lines.append(f"  {CYAN}{label}{RESET}")
            for d in diags:
                col = RED if d.level == "error" else (YEL if d.level == "warning" else "")
                lines.append(f"    {col}{d}{RESET}")
            lines.append("")

        e, w, h = len(self.errors()), len(self.warnings()), len(self.hints())
        ok_str = f"{GRN}OK{RESET}" if self.ok else f"{RED}FAIL{RESET}"
        lines.append(
            f"  {self.rule_count} rules  "
            f"{RED}{e} error(s){RESET}  "
            f"{YEL}{w} warning(s){RESET}  "
            f"{e + w + h} total  "
            f"→ {ok_str}"
        )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rule_count": self.rule_count,
            "source_file": self.source_file,
            "errors":   [_diag_dict(d) for d in self.errors()],
            "warnings": [_diag_dict(d) for d in self.warnings()],
            "hints":    [_diag_dict(d) for d in self.hints()],
        }


def _diag_dict(d: Diagnostic) -> dict[str, Any]:
    return {
        "level": d.level,
        "rule_id": d.rule_id,
        "message": d.message,
        "suggestion": d.suggestion,
        "line": d.line,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Line-number index (maps token position → source line)
# ──────────────────────────────────────────────────────────────────────────────

def _build_line_map(src: str) -> list[int]:
    """Return a list where ``line_map[i]`` is the 1-based line number of char i."""
    lines = [1]
    for ch in src:
        lines.append(lines[-1] + (1 if ch == "\n" else 0))
    return lines


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def validate_source(src: str, source_file: str = "") -> ValidationReport:
    """Parse, compile, and semantically check a rule source string.

    Returns a :class:`ValidationReport` containing all diagnostics.
    """
    report = ValidationReport(source_file=source_file)
    line_map = _build_line_map(src)

    # ── Phase 1: parse ────────────────────────────────────────────────────────
    from agentguard.policy.dsl.parser import parse_rule_source
    from agentguard.models.errors import RuleCompileError

    try:
        asts = parse_rule_source(src)
    except RuleCompileError as exc:
        msg = str(exc)
        line = _guess_line_from_pos(msg, line_map)
        report.diagnostics.append(Diagnostic(
            level="error", rule_id=None, line=line,
            message=f"Parse error: {msg}",
            suggestion=_parse_error_suggestion(msg),
        ))
        return report

    # ── Phase 2: compile ──────────────────────────────────────────────────────
    from agentguard.policy.dsl.compiler import RuleCompiler

    compiled: list[Any] = []
    for ast_node in asts:
        try:
            rule = RuleCompiler().compile(ast_node)
            compiled.append(rule)
        except RuleCompileError as exc:
            msg = str(exc)
            report.diagnostics.append(Diagnostic(
                level="error", rule_id=ast_node.rule_id, line=None,
                message=f"Compile error: {msg}",
                suggestion=_compile_error_suggestion(msg, ast_node),
            ))

    report.rule_count = len(asts)

    # ── Phase 3: semantic checks on each AST ─────────────────────────────────
    seen_ids: set[str] = set()
    for ast_node in asts:
        _check_rule(ast_node, src, line_map, seen_ids, report)

    # ── Phase 4: file-level checks ────────────────────────────────────────────
    _check_file_level(asts, report)

    return report


def validate_file(path: str) -> ValidationReport:
    """Validate a rule file on disk."""
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        r = ValidationReport(source_file=path)
        r.diagnostics.append(Diagnostic(
            level="error", rule_id=None,
            message=f"File not found: {path}",
        ))
        return r
    return validate_source(p.read_text(encoding="utf-8"), source_file=path)


# ──────────────────────────────────────────────────────────────────────────────
# Per-rule semantic checks
# ──────────────────────────────────────────────────────────────────────────────

def _check_rule(ast_node: Any, src: str, line_map: list[int],
                seen_ids: set[str], report: ValidationReport) -> None:
    from agentguard.policy.dsl.ast import (
        BoolOp, Compare, BareFunc, NotOp, ExistsPath, Path, FuncCall,
        TraceClause, SetLit,
    )

    rule_id = ast_node.rule_id
    add = report.diagnostics.append

    # ── duplicate rule IDs ────────────────────────────────────────────────
    if rule_id in seen_ids:
        add(Diagnostic(
            level="warning", rule_id=rule_id,
            message=f"Duplicate rule ID '{rule_id}' — the later rule silently overrides the earlier one.",
            suggestion="Give each rule a unique name, e.g. append a suffix: my_rule_v2.",
        ))
    seen_ids.add(rule_id)

    # ── rule ID naming ────────────────────────────────────────────────────
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*$", rule_id):
        add(Diagnostic(
            level="warning", rule_id=rule_id,
            message=f"Rule ID '{rule_id}' contains unusual characters.",
            suggestion="Use letters, digits, underscores or hyphens only. Example: deny_shell_for_basic",
        ))

    # ── TRACE clause checks ───────────────────────────────────────────────
    if ast_node.trace_clause is not None:
        tc = ast_node.trace_clause
        _check_trace_clause(tc, rule_id, report)
        placeholder_names = {s.name for s in tc.steps}
    else:
        placeholder_names: set[str] = set()

    # ── condition expression ──────────────────────────────────────────────
    _check_expr(ast_node.expr, rule_id, placeholder_names, report)

    # ── metadata ──────────────────────────────────────────────────────────
    meta = ast_node.meta or {}

    if "severity" not in meta:
        add(Diagnostic(
            level="hint", rule_id=rule_id,
            message="Rule has no Severity: metadata.",
            suggestion="Add  Severity: critical/high/medium/low  so dashboards can triage by urgency.",
        ))
    else:
        sev = str(meta["severity"]).lower()
        if sev not in _VALID_SEVERITIES:
            add(Diagnostic(
                level="warning", rule_id=rule_id,
                message=f"Unknown severity value: {sev!r}.",
                suggestion=f"Use one of: {', '.join(sorted(_VALID_SEVERITIES))}",
            ))

    if "category" not in meta:
        add(Diagnostic(
            level="hint", rule_id=rule_id,
            message="Rule has no Category: metadata.",
            suggestion="Add  Category: data_exfiltration  (or similar) for alerting and metrics grouping.",
        ))

    # ── action-specific checks ────────────────────────────────────────────
    action = ast_node.action
    if action.kind == "DEGRADE" and not action.profile:
        add(Diagnostic(
            level="error", rule_id=rule_id,
            message="DEGRADE action is missing a profile name.",
            suggestion=(
                "Specify a profile:  POLICY: DEGRADE(email.send_to_draft)"
            ),
        ))

    if action.kind == "LLM_CHECK":
        add(Diagnostic(
            level="hint", rule_id=rule_id,
            message="Rule uses LLM_CHECK — ensure AGENTGUARD_LLM_API_KEY is set in the runtime environment.",
            suggestion=(
                "Set env vars: AGENTGUARD_LLM_MODEL=gpt-4o  AGENTGUARD_LLM_API_KEY=sk-...\n"
                "Or pass Guard(llm_backend='env') / Guard(llm_backend=LLMBackend(...))."
            ),
        ))
    elif str(meta.get("prompt", "")).strip():
        add(Diagnostic(
            level="warning", rule_id=rule_id,
            message="Prompt: metadata is only used for LLM_CHECK rules.",
            suggestion="Move Prompt: to a rule whose POLICY is LLM_CHECK, or remove it.",
        ))

    # ── v3-only: TRACE without CONDITION uses trivial-true ────────────────
    if ast_node.trace_clause is not None:
        from agentguard.policy.dsl.parser import _TrueExpr
        if isinstance(ast_node.expr, _TrueExpr):
            steps = ast_node.trace_clause.steps
            if len(steps) == 1:
                ph = steps[0].name
                example = f"  CONDITION: {ph}.name == \"dangerous_tool\""
            else:
                src_ph = steps[0].name
                dst_ph = steps[-1].name
                example = (
                    f"  CONDITION: {src_ph}.integrity == \"unfiltered\" "
                    f"AND {dst_ph}.name == \"ExecuteCode\""
                )
            add(Diagnostic(
                level="hint", rule_id=rule_id,
                message="TRACE clause present but no CONDITION — rule fires for any match of the trace pattern.",
                suggestion=(
                    "Add a CONDITION to constrain which matched entries trigger the rule, e.g.:\n"
                    + example
                ),
            ))


def _check_trace_clause(tc: Any, rule_id: str, report: ValidationReport) -> None:
    add = report.diagnostics.append
    names = [s.name for s in tc.steps]

    # Duplicate placeholder names in one TRACE
    if len(names) != len(set(names)):
        seen: set[str] = set()
        dups = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        add(Diagnostic(
            level="warning", rule_id=rule_id,
            message=f"Duplicate placeholder name(s) in TRACE: {', '.join(set(dups))}.",
            suggestion=(
                "Each step must have a unique name. Use descriptive names:\n"
                "  TRACE: Src ->...?-> Mid ->...?-> Dst"
            ),
        ))

    # Check separator semantics
    for step in tc.steps[1:]:
        sep = step.sep
        if sep not in ("->", "-> *", "-> ...", "-> ...?"):
            add(Diagnostic(
                level="error", rule_id=rule_id,
                message=f"Unknown TRACE separator: {sep!r}",
                suggestion=(
                    "Valid separators:\n"
                    "  ->       adjacent (no gap)\n"
                    "  -> * ->  exactly one call between\n"
                    "  -> ... ->  at least one call between\n"
                    "  -> ...? ->  zero or more (anywhere after)"
                ),
            ))


def _check_expr(node: Any, rule_id: str, placeholder_names: set[str],
                report: ValidationReport) -> None:
    """Recursively walk the condition expression and emit semantic diagnostics."""
    if node is None:
        return
    from agentguard.policy.dsl.ast import (
        BoolOp, Compare, BareFunc, NotOp, ExistsPath, Path, FuncCall, SetLit,
    )
    from agentguard.policy.dsl.parser import _TrueExpr

    if isinstance(node, _TrueExpr):
        return
    if isinstance(node, BoolOp):
        _check_expr(node.left, rule_id, placeholder_names, report)
        _check_expr(node.right, rule_id, placeholder_names, report)
    elif isinstance(node, NotOp):
        _check_expr(node.expr, rule_id, placeholder_names, report)
    elif isinstance(node, Compare):
        _check_compare(node, rule_id, placeholder_names, report)
    elif isinstance(node, BareFunc):
        _check_func(node.func, rule_id, placeholder_names, report)
    elif isinstance(node, ExistsPath):
        if not node.source_labels:
            report.diagnostics.append(Diagnostic(
                level="warning", rule_id=rule_id,
                message="exists_path() has no source_label — will always return False.",
                suggestion=(
                    "Specify at least one label:\n"
                    "  exists_path(source.label IN {\"pii/*\"}, max_hops = 6)"
                ),
            ))


def _check_compare(node: Any, rule_id: str, placeholder_names: set[str],
                   report: ValidationReport) -> None:
    from agentguard.policy.dsl.ast import Path, FuncCall, SetLit
    add = report.diagnostics.append

    # Check left-hand side
    if isinstance(node.path, Path):
        _check_path(node.path.parts, rule_id, placeholder_names, report,
                    is_lhs=True, op=node.op, value=node.value)
    elif isinstance(node.path, FuncCall):
        _check_func(node.path, rule_id, placeholder_names, report)

    # Check right-hand side — catch bare enum-like identifiers that should be strings
    if isinstance(node.value, Path) and len(node.value.parts) == 1:
        bare = node.value.parts[0]
        if bare.upper() in {
            "UNFILTERED", "TRUSTED", "INTERNAL", "EXTERNAL", "PRIVILEGED",
            "LOW", "MODERATE", "HIGH", "NONE",
        }:
            add(Diagnostic(
                level="hint", rule_id=rule_id,
                message=f"Bare identifier {bare!r} used as comparison value — will be auto-lowercased.",
                suggestion=(
                    f"For clarity, quote it explicitly:  == \"{bare.lower()}\"\n"
                    "  (AgentGuard auto-lowercases ALL-CAPS bare identifiers, but quoting avoids ambiguity.)"
                ),
            ))

    if isinstance(node.value, FuncCall):
        _check_func(node.value, rule_id, placeholder_names, report)


def _check_path(parts: list[str], rule_id: str, placeholder_names: set[str],
                report: ValidationReport, *, is_lhs: bool = False,
                op: str = "", value: Any = None) -> None:
    add = report.diagnostics.append
    from agentguard.policy.dsl.ast import SetLit

    if not parts:
        return

    prefix = parts[0]

    # v3 placeholder reference
    if placeholder_names and prefix in placeholder_names:
        if len(parts) < 2:
            add(Diagnostic(
                level="warning", rule_id=rule_id,
                message=f"Placeholder '{prefix}' used without a sub-field.",
                suggestion=(
                    f"Access a field of the placeholder, e.g.:\n"
                    f"  {prefix}.name == \"some_tool\"\n"
                    f"  {prefix}.integrity == \"unfiltered\"\n"
                    f"  {prefix}.boundary == \"external\"\n"
                    f"  {prefix}.result == \"restricted\""
                ),
            ))
        elif len(parts) == 2:
            sub = parts[1].lower()
            if sub not in {
                "name", "integrity", "sensitivity", "boundary", "result",
                "tags",
            }:
                # Could be an arg access — acceptable but hint
                add(Diagnostic(
                    level="hint", rule_id=rule_id,
                    message=f"Placeholder field '{prefix}.{parts[1]}' looks like an argument access.",
                    suggestion=(
                        f"Known TRACE placeholder fields: name, integrity, sensitivity, boundary, result.\n"
                        f"If '{parts[1]}' is a tool argument, this is fine — it maps to args['{parts[1]}']."
                    ),
                ))
        return  # no further checks for placeholder paths

    # Standard path checks
    if prefix == "tool":
        if len(parts) >= 2:
            sub = parts[1]
            if sub not in _KNOWN_TOOL_FIELDS:
                # Might be a parameter access — that's OK, but note it
                pass  # tool.<param> is valid and intended
            elif sub == "boundary":
                _check_enum_value(value, VALID_BOUNDARIES, "tool.boundary",
                                  rule_id, report, op)
            elif sub == "sensitivity":
                _check_enum_value(value, VALID_SENSITIVITIES, "tool.sensitivity",
                                  rule_id, report, op)
            elif sub == "integrity":
                _check_enum_value(value, VALID_INTEGRITIES, "tool.integrity",
                                  rule_id, report, op)
        return

    if prefix in ("caller", "principal"):
        if len(parts) >= 2:
            sub = parts[1]
            if sub == "role" and op == "==" and value is not None:
                _check_string_value(value, {"basic", "default", "privileged", "system"},
                                    "principal.role", rule_id, report)
        return

    if prefix in ("target",):
        return

    if prefix in ("allowlist",):
        if len(parts) < 2:
            add(Diagnostic(
                level="warning", rule_id=rule_id,
                message="'allowlist' used without a key — needs allowlist.http, allowlist.email, etc.",
                suggestion="Use  target.domain NOT IN allowlist.http  or  allowlist.email",
            ))
        return

    if prefix == "input":
        return  # handled via function predicates

    if prefix not in _KNOWN_PREFIXES:
        add(Diagnostic(
            level="warning", rule_id=rule_id,
            message=f"Unknown path prefix '{prefix}' in condition.",
            suggestion=(
                f"Known prefixes: {', '.join(sorted(_KNOWN_PREFIXES))}.\n"
                "  Common paths: tool.name, tool.boundary, tool.sensitivity, tool.integrity,\n"
                "    tool.<param>, principal.role, principal.trust_level,\n"
                "    target.domain, allowlist.http, input.has_any_label({{\"pii/*\"}})."
            ),
        ))


def _check_func(func: Any, rule_id: str, placeholder_names: set[str],
                report: ValidationReport) -> None:
    from agentguard.policy.dsl.ast import Path
    add = report.diagnostics.append

    ns   = func.namespace or ""
    name = func.name

    # namespace.name style (e.g. caller.scope_missing)
    full = f"{ns}.{name}" if ns else name

    # ── history_arg / history_result in a TRACE rule ─────────────────────
    # This is the most common pitfall: using history_arg("send_email","addr")
    # to access the CURRENT call's arg, but the current call is NOT in
    # session.trace_rich yet (it's written AFTER evaluation).
    # The correct approach is to use the TRACE placeholder: Mailer.addr.
    if name in ("history_arg", "history_result") and placeholder_names and not ns:
        if func.args:
            queried_tool = str(func.args[0]) if isinstance(func.args[0], str) else None
            if queried_tool:
                # Check if a placeholder likely corresponds to this tool
                # (we can't know for sure at static-analysis time, but warn)
                add(Diagnostic(
                    level="warning", rule_id=rule_id,
                    message=(
                        f"{name}(\"{queried_tool}\", ...) used inside a TRACE rule. "
                        f"history_arg/history_result reads the CACHE which does NOT contain "
                        f"the *current* tool call being evaluated — it's only written AFTER "
                        f"the policy decision. This causes false positives when the queried "
                        f"tool IS the current call."
                    ),
                    suggestion=(
                        f"If you want to access the current tool's args, use the TRACE "
                        f"placeholder instead:\n"
                        f"  Instead of:  history_arg(\"{queried_tool}\", \"param\") == value\n"
                        f"  Use:         Placeholder.param == value\n"
                        f"  (where Placeholder is the TRACE step name bound to \"{queried_tool}\")\n\n"
                        f"  history_arg is correct ONLY for accessing args of a *previous* call "
                        f"that already completed before the current evaluation."
                    ),
                ))

    # input.has_label / input.has_any_label — valid
    if ns == "input" and name in ("has_label", "has_any_label"):
        if not func.args:
            add(Diagnostic(
                level="warning", rule_id=rule_id,
                message=f"{full}() called with no arguments.",
                suggestion='Provide a label pattern:  input.has_any_label({"pii/*", "finance/*"})',
            ))
        return

    # caller.scope_missing
    if ns in ("caller", "principal") and name == "scope_missing":
        if not func.args:
            add(Diagnostic(
                level="warning", rule_id=rule_id,
                message=f"{full}() called with no arguments.",
                suggestion='Provide a scope name:  caller.scope_missing("sensitive_export")',
            ))
        return

    # history_arg / history_result / history_args_match
    if name in ("history_arg", "history_args_match"):
        if len(func.args) < 2:
            add(Diagnostic(
                level="error", rule_id=rule_id,
                message=f"{name}() requires 2 arguments: (tool_name, param_name).",
                suggestion=(
                    f'Usage:  {name}("retrieve_doc", "id")\n'
                    f'         history_args_match("tool", "param", value)'
                ),
            ))
        return

    if name == "history_result":
        if len(func.args) < 1:
            add(Diagnostic(
                level="error", rule_id=rule_id,
                message="history_result() requires 1 argument: (tool_name).",
                suggestion='Usage:  history_result("classify_doc") == "restricted"',
            ))
        return

    # trace()
    if name == "trace" and not ns:
        if not func.args:
            add(Diagnostic(
                level="error", rule_id=rule_id,
                message="trace() called with no pattern string.",
                suggestion=(
                    'Provide a pattern:  trace("db.query ->...? -> email.send")\n'
                    'Valid separators: ->, -> * ->, -> ... ->, -> ...? ->'
                ),
            ))
            return
        pat = func.args[0]
        if isinstance(pat, str):
            _check_trace_pattern_string(pat, rule_id, report)
        return

    # exists_path is handled via ExistsPath AST node, but also callable style
    if name in ("exists_path", "EXISTS_PATH"):
        return

    # Unknown top-level function
    if not ns and name not in _KNOWN_FUNCS:
        add(Diagnostic(
            level="warning", rule_id=rule_id,
            message=f"Unknown predicate function '{name}'.",
            suggestion=(
                f"Known predicates: {', '.join(sorted(_KNOWN_FUNCS))}.\n"
                "If this is a custom function, ensure it is registered in the compiler's _FUNC_TABLE."
            ),
        ))


def _check_trace_pattern_string(pat: str, rule_id: str, report: ValidationReport) -> None:
    """Validate a string passed to trace('...')."""
    add = report.diagnostics.append
    try:
        from agentguard.policy.dsl.trace_pattern import compile_trace_pattern
        compile_trace_pattern(pat)
    except Exception as exc:
        add(Diagnostic(
            level="error", rule_id=rule_id,
            message=f"Invalid trace() pattern {pat!r}: {exc}",
            suggestion=(
                "Correct format examples:\n"
                '  trace("db.query -> email.send")             # adjacent\n'
                '  trace("db.query -> * -> email.send")        # exactly one between\n'
                '  trace("db.query -> ... -> email.send")      # at least one between\n'
                '  trace("db.query ->...? -> email.send")      # anywhere after'
            ),
        ))


def _check_enum_value(value: Any, valid: set[str], field_name: str,
                      rule_id: str, report: ValidationReport, op: str) -> None:
    from agentguard.policy.dsl.ast import Path, SetLit
    if value is None:
        return
    add = report.diagnostics.append

    candidates: list[str] = []
    if isinstance(value, str):
        candidates = [value.lower()]
    elif isinstance(value, Path) and len(value.parts) == 1:
        candidates = [value.parts[0].lower()]
    elif isinstance(value, SetLit):
        candidates = [v.lower() for v in value.items]

    for v in candidates:
        if v not in valid:
            add(Diagnostic(
                level="warning", rule_id=rule_id,
                message=f"'{v}' is not a valid value for {field_name}.",
                suggestion=f"Valid values: {', '.join(sorted(valid))}",
            ))


def _check_string_value(value: Any, valid: set[str], field_name: str,
                        rule_id: str, report: ValidationReport) -> None:
    from agentguard.policy.dsl.ast import Path
    if value is None:
        return
    raw = None
    if isinstance(value, str):
        raw = value
    elif isinstance(value, Path) and len(value.parts) == 1:
        raw = value.parts[0]
    if raw and raw not in valid:
        report.diagnostics.append(Diagnostic(
            level="hint", rule_id=rule_id,
            message=f"'{raw}' is an unusual value for {field_name}.",
            suggestion=f"Typical values: {', '.join(sorted(valid))}",
        ))


# ──────────────────────────────────────────────────────────────────────────────
# File-level checks
# ──────────────────────────────────────────────────────────────────────────────

def _check_file_level(asts: list[Any], report: ValidationReport) -> None:
    add = report.diagnostics.append

    if not asts:
        add(Diagnostic(
            level="warning", rule_id=None,
            message="File contains no rules.",
            suggestion=(
                "A v3 rule looks like:\n\n"
                "    RULE: my_rule\n"
                "    CONDITION: principal.trust_level < 2\n"
                "    POLICY: DENY\n"
                "    Severity: high\n"
                "    Category: capability\n\n"
                "Or with a TRACE clause:\n\n"
                "    RULE: data_exfil\n"
                "    TRACE: Src ->...?-> Dst\n"
                "    CONDITION: Src.sensitivity == \"high\" AND Dst.boundary == \"external\"\n"
                "    POLICY: LLM_CHECK\n"
                "    Prompt: \"Escalate ambiguous outbound data flows.\"\n"
                "    Severity: critical\n"
                "    Category: data_exfiltration"
            ),
        ))
        return

    # Hint about missing DENY rules in large files
    actions = [a.action.kind for a in asts]
    if len(asts) > 5 and "DENY" not in actions:
        add(Diagnostic(
            level="hint", rule_id=None,
            message="No DENY rules in this file — all decisions are ALLOW/LLM_CHECK/DEGRADE.",
            suggestion="Consider adding hard-deny rules for the most critical scenarios.",
        ))


# ──────────────────────────────────────────────────────────────────────────────
# Error message → suggestion helpers
# ──────────────────────────────────────────────────────────────────────────────

def _guess_line_from_pos(msg: str, line_map: list[int]) -> int | None:
    m = re.search(r"at pos (\d+)", msg)
    if m:
        pos = int(m.group(1))
        if pos < len(line_map):
            return line_map[pos]
    return None


def _parse_error_suggestion(msg: str) -> str:
    msg_lower = msg.lower()

    if "expected kw/rule" in msg_lower or "expected rule" in msg_lower:
        return "Every rule must start with  RULE: rule_name  followed by POLICY: ACTION."
    if "expected punc/:" in msg_lower:
        return "Rules require a colon after RULE:  e.g.  RULE: my_rule"
    if "unexpected character" in msg_lower:
        ch_m = re.search(r"unexpected character (.+?) at pos", msg)
        ch = ch_m.group(1) if ch_m else "?"
        return (
            f"Character {ch} is not valid in this position.\n"
            "Common causes:\n"
            "  • Using % or $ — not supported\n"
            "  • Missing closing quote  \" or '\n"
            "  • Missing closing parenthesis )\n"
            "  • Typo in a keyword (POLICY, CONDITION, TRACE, etc.)"
        )
    if "unterminated string" in msg_lower:
        return "A string literal is missing its closing quote. Check for unmatched \" or '."
    if "expected kw/in" in msg_lower:
        return (
            "Expected 'IN' keyword, e.g.:\n"
            '  tool.name IN {"send_email", "email.send"}\n'
            '  target.domain NOT IN allowlist.http'
        )
    if "expected '->'  after" in msg_lower or "trace" in msg_lower:
        return (
            "TRACE clause syntax error.  Valid forms:\n"
            "  TRACE: T                         (single step — binds to current call)\n"
            "  TRACE: Src -> Dst                (adjacent)\n"
            "  TRACE: Src -> * -> Dst           (exactly one between)\n"
            "  TRACE: Src -> ... -> Dst         (at least one between)\n"
            "  TRACE: Src ->...?-> Dst          (anywhere after)"
        )
    if "at least one placeholder" in msg_lower:
        return "A TRACE clause needs at least one placeholder step."
    if "policy" in msg_lower:
        return (
            "Rules require a POLICY: line.\n"
            "Valid actions: DENY, ALLOW, HUMAN_CHECK, LLM_CHECK, DEGRADE(...)\n"
            "Example:  POLICY: LLM_CHECK"
        )
    return (
        "DSL quick reference:\n"
        "    RULE: rule_name\n"
        "    [ON:        tool_call[.requested|.completed|.failed][(pattern)]]\n"
        "    [TRACE:     T]  or  [TRACE: A ->...?-> B]\n"
        "    [CONDITION: <expr>]\n"
        "    POLICY: DENY | ALLOW | HUMAN_CHECK | LLM_CHECK | DEGRADE(profile)\n"
        "    Severity: critical | high | medium | low\n"
        "    Category: <free text>\n"
        "    Reason:   \"<free text>\""
    )


def _compile_error_suggestion(msg: str, ast_node: Any) -> str:
    msg_lower = msg.lower()
    if "unknown action" in msg_lower:
        return (
            f"Valid actions: {', '.join(sorted(_VALID_ACTIONS))}.\n"
            "DEGRADE requires a profile:  DEGRADE(email.send_to_draft)"
        )
    if "unsupported expression" in msg_lower:
        return (
            "The expression contains an unsupported AST node.\n"
            "Ensure all conditions use supported predicates and path expressions."
        )
    return "Review the rule's CONDITION clause for unsupported constructs."
