"""Recursive-descent parser for the AgentGuard rule DSL (v3 only).

Syntax
------

    RULE: rule_name
    [ON:        tool_call[.requested|.completed|.failed][(tool_pattern)]]
    [TRACE:     Step1 [-> Step2 ...]]
    [CONDITION: expr]
    POLICY:     DENY | ALLOW | HUMAN_CHECK | LLM_CHECK | DEGRADE(profile)
    [Severity:  critical | high | medium | low]
    [Category:  free text]
    [Reason:    "free text"]

TRACE + ON unification
----------------------
When a TRACE clause is present, the current event is the *last* step in
the trace.  ``ON:`` therefore constrains the event type of that last step,
so ``ON: tool_call.requested`` means "this is a pre-execution intercept at
the tail of the call chain".  Single-point rules (no TRACE) behave
identically; they simply match a one-entry chain.

TRACE placeholder fields in CONDITION
--------------------------------------
    Placeholder.name         tool_name of the matched entry
    Placeholder.integrity    label.integrity
    Placeholder.sensitivity  label.sensitivity
    Placeholder.boundary     label.boundary
    Placeholder.result       return value
    Placeholder.<param>      args[param]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from agentguard.models.errors import RuleCompileError
from agentguard.policy.dsl.ast import (
    Action,
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

# ============================ tokenizer ============================

KEYWORDS = {
    "RULE", "ON", "WITH",
    "AND", "OR", "NOT", "IN", "TO",
    "DENY", "ALLOW", "HUMAN_CHECK", "LLM_CHECK", "DEGRADE",
    "EXISTS_PATH",
    "MATCHES", "CONTAINS",
    "true", "false", "TRUE", "FALSE",
    "TRACE", "CONDITION", "POLICY",
}

# Known obligation keywords (can be extended).
OBLIGATION_KINDS = {
    "REDACT", "AUDIT", "REQUIRE_TARGET_IN", "MASK_FIELDS", "RATE_LIMIT",
}

# Functions that return a boolean signal when used bare as a predicate.
BARE_SIGNAL_FUNCS = {
    "goal_drift_detected", "scope_expansion_detected",
    "suspicious_exfil_pattern", "high_entropy_payload_detected",
    "goal_changed_from_initial",
    "upstream_contains_tool", "upstream_contains_any_tool",
    "derived_from_tool", "tool_sequence_matches",
}


@dataclass
class Token:
    kind: str     # IDENT | STRING | NUMBER | OP | PUNC | KW
    value: Any
    pos: int


def _tokenize(src: str) -> list[Token]:
    i, n = 0, len(src)
    toks: list[Token] = []
    while i < n:
        ch = src[i]
        if ch in " \t\r\n":
            i += 1; continue
        if ch == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src[i:i + 2] in ("==", "!=", "<=", ">="):
            toks.append(Token("OP", src[i:i + 2], i)); i += 2; continue
        if ch in "<>":
            toks.append(Token("OP", ch, i)); i += 1; continue
        if ch in "(){},=.:":
            toks.append(Token("PUNC", ch, i)); i += 1; continue
        if ch == '"' or ch == "'":
            quote = ch; j = i + 1; out: list[str] = []
            while j < n and src[j] != quote:
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    # Recognise the standard JSON-ish escapes; pass any other
                    # backslash-escape through *verbatim* (so regex meta-
                    # characters like ``\d``, ``\s``, ``\.`` survive).
                    if nxt == "n":
                        out.append("\n")
                    elif nxt == "t":
                        out.append("\t")
                    elif nxt == "r":
                        out.append("\r")
                    elif nxt == "0":
                        out.append("\0")
                    elif nxt in ("\\", '"', "'"):
                        out.append(nxt)
                    else:
                        out.append("\\")
                        out.append(nxt)
                    j += 2
                    continue
                out.append(src[j]); j += 1
            if j >= n:
                raise RuleCompileError(f"unterminated string at pos {i}")
            toks.append(Token("STRING", "".join(out), i))
            i = j + 1; continue
        if ch.isdigit() or (ch == "-" and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            raw = src[i:j]
            val: float | int = float(raw) if "." in raw else int(raw)
            toks.append(Token("NUMBER", val, i))
            i = j; continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"
                              # Allow hyphens in identifiers for placeholder names
                              # like Tool-A, Source-Node etc., but only when the
                              # char after the hyphen is alphanumeric (not '>').
                              or (src[j] == "-" and j + 1 < n
                                  and src[j + 1] != ">"
                                  and (src[j + 1].isalnum() or src[j + 1] == "_"))):
                j += 1
            word = src[i:j]
            if word in KEYWORDS:
                toks.append(Token("KW", word, i))
            else:
                toks.append(Token("IDENT", word, i))
            i = j; continue
        if ch == "*":
            toks.append(Token("PUNC", "*", i)); i += 1; continue
        if ch == "?":
            toks.append(Token("PUNC", "?", i)); i += 1; continue
        if ch == "-":
            # '->' arrow (not a negative number)
            if i + 1 < n and src[i + 1] == ">":
                toks.append(Token("PUNC", "->", i)); i += 2; continue
            # '-' before digit → negative number (handled above already, but keep guard)
            raise RuleCompileError(f"unexpected character {ch!r} at pos {i}")
        raise RuleCompileError(f"unexpected character {ch!r} at pos {i}")
    toks.append(Token("EOF", None, len(src)))
    return toks


# ============================ parser ============================

# Sentinel used when a v3 rule has a TRACE clause but no explicit CONDITION.
# The compiler recognises this object and replaces it with "True".
class _TrueExpr:
    """Always-true expression placeholder."""

_TRUE_EXPR = _TrueExpr()

class _Parser:
    def __init__(self, toks: list[Token]) -> None:
        self.toks = toks
        self.i = 0

    def peek(self, off: int = 0) -> Token:
        return self.toks[self.i + off]

    def eat(self, kind: str, value: Any = None) -> Token:
        t = self.peek()
        if t.kind != kind or (value is not None and t.value != value):
            raise RuleCompileError(
                f"expected {kind}{'/' + str(value) if value else ''}, "
                f"got {t.kind}/{t.value} at {t.pos}")
        self.i += 1
        return t

    def accept(self, kind: str, value: Any = None) -> Token | None:
        t = self.peek()
        if t.kind == kind and (value is None or t.value == value):
            self.i += 1
            return t
        return None

    # ---------- grammar ----------
    def parse_rules(self) -> list[RuleAST]:
        rules: list[RuleAST] = []
        while self.peek().kind != "EOF":
            rules.append(self.parse_rule())
        return rules

    def parse_rule(self) -> RuleAST:
        self.eat("KW", "RULE")
        self.eat("PUNC", ":")
        return self._parse_rule_v3()

    # ─────────────────────────────────────────────────────────────────────
    # v3 rule parser
    # ─────────────────────────────────────────────────────────────────────

    # Metadata keys recognised at the rule level (case-insensitive).
    _V3_META_KEYS = {"severity", "category", "reason", "prompt", "priority", "ttl_ms"}

    #: Keywords that terminate the CONDITION / TRACE clause scanning.
    _V3_STOP_KEYS = {"POLICY", "TRACE", "CONDITION", "ON", "RULE"}

    def _parse_rule_v3(self) -> RuleAST:
        """Parse the rule body after ``RULE:``.

        ON:        optional; constrains event type.  In a TRACE rule this
                   applies to the *last* step (the current call).
        TRACE:     optional; named placeholder chain (1+ steps).
        CONDITION: optional; expression over placeholders / event fields.
        POLICY:    required; the enforcement action.
        """
        name = self._eat_v3_name()   # allows hyphens in rule names

        # Optional ON: clause
        pattern, subtype = "*", ""
        if self._v3_accept_key("ON"):
            pattern, subtype = self._parse_event_match()

        # Optional TRACE: clause
        trace_clause: TraceClause | None = None
        if self._v3_accept_key("TRACE"):
            trace_clause = self._parse_trace_clause()

        # Optional CONDITION: clause
        expr: Any = None
        if self._v3_accept_key("CONDITION"):
            expr = self._parse_expr()

        # Mandatory POLICY: clause
        self._v3_require_key("POLICY")
        action = self._parse_action()

        # Remaining lines are metadata: Key: value
        meta = self._parse_v3_meta()

        # If no explicit CONDITION but there's a TRACE, the predicate is trivially
        # True (the trace clause itself is compiled into the predicate later).
        if expr is None:
            expr = _TRUE_EXPR

        return RuleAST(
            rule_id=name,
            tool_pattern=pattern,
            expr=expr,
            action=action,
            event_subtype=subtype,
            meta=meta,
            trace_clause=trace_clause,
        )

    def _eat_v3_name(self) -> str:
        """Eat a name token — supports hyphenated names like Tool-A."""
        # With the updated tokenizer, Tool-A is emitted as a single IDENT token.
        # For robustness we also handle the fallback of separate tokens.
        parts = [self.eat("IDENT").value]
        return "".join(parts)

    def _v3_accept_key(self, keyword: str) -> bool:
        """Accept ``KW keyword`` followed by ``:``.  Returns True if consumed."""
        if self.peek().kind == "KW" and self.peek().value == keyword:
            if self.peek(1).kind == "PUNC" and self.peek(1).value == ":":
                self.i += 2
                return True
        # Also accept bare IDENT when the keyword is a v3-only one (e.g. TRACE,
        # CONDITION, POLICY) — allows lowercase variants like "Severity:".
        if self.peek().kind == "IDENT" and self.peek().value.upper() == keyword:
            if self.peek(1).kind == "PUNC" and self.peek(1).value == ":":
                self.i += 2
                return True
        return False

    def _v3_require_key(self, keyword: str) -> None:
        if not self._v3_accept_key(keyword):
            t = self.peek()
            raise RuleCompileError(
                f"expected '{keyword}:' in v3 rule, got {t.kind}/{t.value} at pos {t.pos}"
            )

    def _parse_trace_clause(self) -> TraceClause:
        """Parse ``Name1 -> [gap ->] Name2 -> ...`` after the TRACE: keyword.

        Separator tokens recognised:
            ``->``          adjacent
            ``-> * ->``     exactly one between
            ``-> ... ->``   at-least-one between
            ``-> ...? ->``  zero-or-more between (anywhere after)

        Placeholder names can be CamelCase or include hyphens (Tool-A).
        """
        steps: list[TraceStep] = []
        sep = ""

        while True:
            # Each step: a placeholder name (possibly hyphenated)
            if self.peek().kind not in ("IDENT", "KW"):
                break
            name = self._eat_v3_name()
            steps.append(TraceStep(name=name, sep=sep))
            sep = ""

            # Look ahead for separator
            # Separator starts with OP "-" OP ">"  (i.e. the two-char -> token may be
            # split in our tokenizer since we only added ":" as PUNC, and "->" is two
            # chars that are separate).  Actually our tokenizer handles "==" and "!="
            # as OP but not "->". We need to handle that.
            # In the current tokenizer, '-' followed by '>' will be:
            #   '-' → unrecognised unless starts a negative number
            #   '>' → OP ">"
            # So we need to detect the pattern OP "-" (as part of number rejection) …
            # Actually '-' is only consumed as a number if followed by a digit.
            # Otherwise it falls through to the error.  We handle it here by detecting
            # OP ">" after an implicit "-".  A cleaner fix: emit "->" as a single PUNC.
            # For now we patch the tokenizer result detection:
            if not self._try_consume_arrow():
                break

            # After '->', check for gap operators encoded as IDENT/PUNC
            gap = self._try_consume_gap()
            if gap:
                # gap operator must be followed by another '->'
                if not self._try_consume_arrow():
                    raise RuleCompileError(
                        f"expected '->' after '{gap}' in TRACE clause"
                    )
                sep = f"-> {gap}"
            else:
                sep = "->"

        if len(steps) < 1:
            raise RuleCompileError(
                "TRACE clause must have at least one placeholder step"
            )
        return TraceClause(steps=steps)

    def _try_consume_arrow(self) -> bool:
        """Consume a '->' token.  Returns True if consumed."""
        if self.peek().kind == "PUNC" and self.peek().value == "->":
            self.i += 1
            return True
        return False

    def _try_consume_gap(self) -> str:
        """Try to consume a gap operator: '...?', '...', or '*'.  Returns the operator or ''."""
        t = self.peek()
        # '...' is three PUNC '.' tokens, optionally followed by PUNC '?'
        if (t.kind == "PUNC" and t.value == "."
                and self.peek(1).kind == "PUNC" and self.peek(1).value == "."
                and self.peek(2).kind == "PUNC" and self.peek(2).value == "."):
            self.i += 3
            if self.peek().kind == "PUNC" and self.peek().value == "?":
                self.i += 1
                return "...?"
            return "..."
        if t.kind == "PUNC" and t.value == "*":
            self.i += 1
            return "*"
        return ""

    def _parse_v3_meta(self) -> dict[str, Any]:
        """Parse remaining ``Key: value`` metadata lines after POLICY clause.

        Recognised patterns:
            Severity: critical
            Category: "data_exfiltration"
            Reason: "some text"
            Prompt: "custom LLM reviewer instructions"
            Priority: 10
        """
        meta: dict[str, Any] = {}
        while True:
            t = self.peek()
            if t.kind == "EOF":
                break
            # A v3 metadata line is: IDENT ":" value
            # But we must not consume the start of the next RULE.
            if t.kind == "KW" and t.value == "RULE":
                break
            if t.kind not in ("IDENT", "KW"):
                break
            key_tok = self.peek()
            key = key_tok.value.lower()
            # Only consume if followed by ':'
            if self.peek(1).kind != "PUNC" or self.peek(1).value != ":":
                break
            self.i += 2   # consume key + ':'
            val = self._parse_value()
            # Convert single-part Path objects (bare identifiers like `critical`)
            # to plain strings so metadata is always string/number/bool.
            from agentguard.policy.dsl.ast import Path as _Path
            if isinstance(val, _Path) and len(val.parts) == 1:
                val = val.parts[0]
            meta[key] = val
            # optional comma separator between meta entries
            self.accept("PUNC", ",")
        return meta

    def _parse_event_match(self) -> tuple[str, str]:
        """Parse event match expressions.  Returns (tool_pattern, event_subtype).

        Supported forms:
          tool_call(pattern)             → (pattern, "")       v1
          tool_call.*                    → ("*", "")           v1 wildcard
          tool_call.requested            → ("*", "requested")  v2 subtype-only
          tool_call.requested(pattern)   → (pattern, "requested")  v2 combined
        """
        t = self.eat("IDENT")
        if t.value != "tool_call":
            raise RuleCompileError(f"expected 'tool_call' at pos {t.pos}, got {t.value!r}")
        # v2 form: tool_call.<subtype>
        if self.accept("PUNC", "."):
            sub = self.eat("IDENT").value
            # optionally followed by (pattern)
            if self.peek().kind == "PUNC" and self.peek().value == "(":
                self.i += 1
                pattern = self._parse_tool_pattern()
                self.eat("PUNC", ")")
                return pattern, sub
            return "*", sub
        # legacy form: tool_call(pattern)
        self.eat("PUNC", "(")
        pattern = self._parse_tool_pattern()
        self.eat("PUNC", ")")
        return pattern, ""

    def _parse_tool_pattern(self) -> str:
        parts: list[str] = []
        if self.accept("PUNC", "*"):
            return "*"
        parts.append(self.eat("IDENT").value)
        while self.accept("PUNC", "."):
            if self.accept("PUNC", "*"):
                parts.append("*"); break
            parts.append(self.eat("IDENT").value)
        return ".".join(parts)

    def _parse_action(self) -> Action:
        t = self.peek()
        # v3 allows IDENT variants (e.g. "LLM Check" written as two tokens, or
        # case-insensitive keywords).  Normalise to uppercase KW.
        if t.kind == "IDENT" and t.value.upper() in (
            "DENY", "ALLOW", "HUMAN_CHECK", "LLM_CHECK", "DEGRADE"
        ):
            # Coerce to KW
            t = Token("KW", t.value.upper(), t.pos)
            self.i += 1
        elif t.kind != "KW":
            raise RuleCompileError(f"expected action keyword at pos {t.pos}")
        else:
            self.i += 1

        action: Action
        if t.value in ("DENY", "ALLOW", "HUMAN_CHECK", "LLM_CHECK"):
            action = Action(kind=t.value)
        elif t.value == "DEGRADE":
            # new form: DEGRADE TO "tool_name"
            if self.accept("KW", "TO"):
                name_tok = self.eat("STRING")
                action = Action(kind="DEGRADE", profile=name_tok.value)
            else:
                # legacy form: DEGRADE(dotted.name)
                self.eat("PUNC", "(")
                parts = [self.eat("IDENT").value]
                while self.accept("PUNC", "."):
                    parts.append(self.eat("IDENT").value)
                self.eat("PUNC", ")")
                action = Action(kind="DEGRADE", profile=".".join(parts))
        else:
            raise RuleCompileError(f"unknown action {t.value!r} at pos {t.pos}")

        # Action-level obligations: THEN ... WITH REDACT(fields={...}), AUDIT(...)
        # Distinguished from rule-level metadata by the *next* token after WITH:
        # rule-level uses IDENT '=', action-level uses IDENT '('.
        if self.peek().kind == "KW" and self.peek().value == "WITH":
            if self._looks_like_action_obligations():
                self.i += 1
                action.obligations = self._parse_obligations()
        return action

    def _looks_like_action_obligations(self) -> bool:
        # peek past WITH
        if self.peek(1).kind != "IDENT":
            return False
        nxt = self.peek(2)
        return nxt.kind == "PUNC" and nxt.value == "("

    def _parse_obligations(self) -> list[ObligationAST]:
        out: list[ObligationAST] = []
        out.append(self._parse_one_obligation())
        while self.accept("PUNC", ","):
            out.append(self._parse_one_obligation())
        return out

    def _parse_one_obligation(self) -> ObligationAST:
        kind_tok = self.eat("IDENT")
        kind = kind_tok.value.upper()
        self.eat("PUNC", "(")
        kwargs: dict[str, Any] = {}
        if not self.accept("PUNC", ")"):
            self._parse_kv_into(kwargs)
            while self.accept("PUNC", ","):
                self._parse_kv_into(kwargs)
            self.eat("PUNC", ")")
        return ObligationAST(kind=kind, args=kwargs)

    def _parse_kv_into(self, dst: dict[str, Any]) -> None:
        key = self.eat("IDENT").value
        self.eat("PUNC", "=")
        dst[key] = self._parse_value()

    # -------- expressions --------
    def _parse_expr(self) -> Any:
        return self._parse_or()

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while self.accept("KW", "OR"):
            right = self._parse_and()
            left = BoolOp("OR", left, right)
        return left

    def _parse_and(self) -> Any:
        left = self._parse_not()
        while self.accept("KW", "AND"):
            right = self._parse_not()
            left = BoolOp("AND", left, right)
        return left

    def _parse_not(self) -> Any:
        if self.accept("KW", "NOT"):
            inner = self._parse_not()
            return NotOp(inner)
        return self._parse_atom()

    def _parse_atom(self) -> Any:
        if self.accept("PUNC", "("):
            e = self._parse_expr()
            self.eat("PUNC", ")")
            return e
        # EXISTS_PATH (legacy KW) or lowercase ``exists_path`` identifier.
        if self.accept("KW", "EXISTS_PATH"):
            return self._parse_exists_path()
        if self.peek().kind == "IDENT" and self.peek().value == "exists_path" \
                and self.peek(1).kind == "PUNC" and self.peek(1).value == "(":
            self.i += 1
            return self._parse_exists_path()
        return self._parse_bare_or_compare()

    def _parse_bare_or_compare(self) -> Any:
        """Parse ``path (compare_tail)?`` where path may be a function call."""
        left = self._parse_path_or_func()
        t = self.peek()
        # Compare tail?
        if t.kind == "KW" and t.value == "IN":
            self.i += 1
            return Compare(path=left, op="IN", value=self._parse_value())
        if t.kind == "KW" and t.value == "NOT":
            self.i += 1
            self.eat("KW", "IN")
            return Compare(path=left, op="NOT_IN", value=self._parse_value())
        if t.kind == "KW" and t.value == "MATCHES":
            self.i += 1
            return Compare(path=left, op="MATCHES", value=self._parse_value())
        if t.kind == "KW" and t.value == "CONTAINS":
            self.i += 1
            return Compare(path=left, op="CONTAINS", value=self._parse_value())
        if t.kind == "OP":
            self.i += 1
            return Compare(path=left, op=t.value, value=self._parse_value())
        # No tail → must be a bare predicate.
        if isinstance(left, FuncCall):
            return BareFunc(func=left)
        raise RuleCompileError(
            f"expected operator or IN after path {left} at pos {t.pos}, "
            f"got {t.kind}/{t.value}")

    def _parse_path_or_func(self) -> Any:
        """Returns Path or FuncCall."""
        parts = [self.eat("IDENT").value]
        while self.accept("PUNC", "."):
            # Stop if we see *.  (Should not happen in expressions.)
            if self.peek().kind == "PUNC" and self.peek().value == "*":
                break
            parts.append(self.eat("IDENT").value)
        # Function call?
        if self.accept("PUNC", "("):
            args, kwargs = self._parse_call_args()
            self.eat("PUNC", ")")
            # namespace = everything except the last part; name = last part.
            if len(parts) == 1:
                ns, name = "", parts[0]
            else:
                ns, name = ".".join(parts[:-1]), parts[-1]
            return FuncCall(name=name, args=args, kwargs=kwargs, namespace=ns)
        return Path(parts)

    def _parse_call_args(self) -> tuple[list[Any], dict[str, Any]]:
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        if self.peek().kind == "PUNC" and self.peek().value == ")":
            return args, kwargs
        while True:
            # kwarg?  IDENT '=' value
            if (self.peek().kind == "IDENT"
                    and self.peek(1).kind == "PUNC"
                    and self.peek(1).value == "="):
                key = self.eat("IDENT").value
                self.eat("PUNC", "=")
                kwargs[key] = self._parse_value()
            else:
                args.append(self._parse_value())
            if not self.accept("PUNC", ","):
                break
        return args, kwargs

    def _parse_value(self) -> Any:
        t = self.peek()
        if t.kind == "STRING":
            self.i += 1; return t.value
        if t.kind == "NUMBER":
            self.i += 1; return t.value
        if t.kind == "KW" and t.value in ("true", "TRUE"):
            self.i += 1; return True
        if t.kind == "KW" and t.value in ("false", "FALSE"):
            self.i += 1; return False
        if t.kind == "PUNC" and t.value == "{":
            return self._parse_set_lit()
        if t.kind == "IDENT":
            return self._parse_path_or_func()
        raise RuleCompileError(f"expected value at pos {t.pos}, got {t.kind}/{t.value}")

    def _parse_set_lit(self) -> SetLit:
        self.eat("PUNC", "{")
        items: list[str] = []
        if not self.accept("PUNC", "}"):
            items.append(self._parse_str_item())
            while self.accept("PUNC", ","):
                items.append(self._parse_str_item())
            self.eat("PUNC", "}")
        return SetLit(items=items)

    def _parse_str_item(self) -> str:
        t = self.peek()
        if t.kind == "STRING":
            self.i += 1
            return t.value
        if t.kind == "IDENT":
            self.i += 1
            return t.value
        raise RuleCompileError(f"expected string inside set at pos {t.pos}")

    def _parse_exists_path(self) -> ExistsPath:
        self.eat("PUNC", "(")
        node = ExistsPath(source_labels=[])
        while True:
            # Accept ``source_label`` OR ``source.label`` as the keyword for
            # the labels argument — matches the suggestion DSL style.
            first = self.eat("IDENT")
            key = first.value
            if self.peek().kind == "PUNC" and self.peek().value == ".":
                self.i += 1
                key = key + "." + self.eat("IDENT").value
            if key in ("source_label", "source.label"):
                self.eat("KW", "IN")
                sl = self._parse_set_lit()
                node.source_labels = sl.items
            else:
                self.eat("PUNC", "=")
                val = self._parse_value()
                if key == "max_hops" and isinstance(val, int):
                    node.max_hops = val
                elif key == "sink":
                    node.sink = str(val) if not isinstance(val, Path) else str(val)
                elif key == "over":
                    node.over = str(val) if not isinstance(val, Path) else str(val)
            if not self.accept("PUNC", ","):
                break
        self.eat("PUNC", ")")
        return node


def parse_rule_source(src: str) -> list[RuleAST]:
    toks = _tokenize(src)
    rules = _Parser(toks).parse_rules()

    text = (src).replace("\r\n", "\n").strip()
    blocks = re.split(r"(?=^RULE:\s*)", text, flags=re.MULTILINE)
    blocks = [block.strip() for block in blocks if block.strip().startswith("RULE:")]
    for i, r in enumerate(rules):
        r.source = src
        r.source_block = blocks[i] if i < len(blocks) else ""
    return rules


def parse_rules(*sources: str) -> list[RuleAST]:
    out: list[RuleAST] = []
    for s in sources:
        out.extend(parse_rule_source(s))
    return out
