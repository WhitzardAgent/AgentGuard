"""Trace pattern matcher.

Four primitives over the chronological tool-call sequence of a session:

    A -> B            adjacent: A immediately followed by B
    A -> * -> B       exactly one tool call between A and B
    A -> ... -> B     non-empty gap: at least one tool call between A and B
    A -> ...? -> B    optional gap: zero or more tool calls between A and B
                      (i.e., A precedes B somewhere later, possibly adjacent)

A pattern is a chain of one or more steps, e.g.

    db.query -> ... -> file.write -> http.post

Implementation: the chronological sequence is encoded as a comma-joined
string ``"db.query,file.write,http.post"`` and the pattern compiles to
a regex over that string. Tool names are regex-escaped so dots and other
metacharacters are matched literally.

Usage:

    matcher = compile_trace_pattern("db.query -> ...? -> http.post")
    matcher(["other_tool", "db.query", "file.read", "http.post"])  # → True
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from typing import Callable, NamedTuple


class TracePatternError(ValueError):
    """Raised when a trace pattern cannot be parsed."""


class _Step(NamedTuple):
    tool: str
    # Separator from previous step, or "" for the first step.
    sep: str


# Recognised separators (longest first, so '...?' beats '...').
_SEPARATORS = ("->", "-> *", "-> ...?", "-> ...")
_SEP_TOKEN_PATTERN = re.compile(
    r"->\s*(?:\*|\.\.\.\?|\.\.\.)?"
)


def _tokenize(pattern: str) -> list[_Step]:
    """Split ``pattern`` into steps annotated with the preceding separator.

    Examples:
        ``"A -> B"``               → [(A, ""), (B, "->")]
        ``"A -> * -> B"``          → [(A, ""), (B, "-> *")]
        ``"A -> ... -> B -> C"``   → [(A, ""), (B, "-> ..."), (C, "->")]
    """
    text = pattern.strip()
    if not text:
        raise TracePatternError("empty trace pattern")

    steps: list[_Step] = []
    pos = 0
    n = len(text)
    expect_step = True
    pending_sep = ""

    while pos < n:
        if text[pos].isspace():
            pos += 1
            continue
        if expect_step:
            # Read a tool-name token: letters / digits / dots / underscores / dashes / colons / slashes
            m = re.match(r"[A-Za-z_][\w\.\-:/]*", text[pos:])
            if not m:
                raise TracePatternError(
                    f"expected tool name at position {pos}: {text[pos:pos+16]!r}"
                )
            tool = m.group(0)
            steps.append(_Step(tool=tool, sep=pending_sep))
            pending_sep = ""
            pos += m.end()
            expect_step = False
            continue
        # Otherwise, expect a separator before the next step.
        if not text.startswith("->", pos):
            raise TracePatternError(
                f"expected '->' at position {pos}: {text[pos:pos+16]!r}"
            )
        pos += 2
        # Skip whitespace, then look for optional gap operator.
        while pos < n and text[pos].isspace():
            pos += 1
        gap = ""
        if pos < n:
            if text.startswith("...?", pos):
                gap = "...?"
                pos += 4
            elif text.startswith("...", pos):
                gap = "..."
                pos += 3
            elif text[pos] == "*":
                gap = "*"
                pos += 1
        if gap:
            # After the gap operator, require another '->' before the next tool.
            while pos < n and text[pos].isspace():
                pos += 1
            if not text.startswith("->", pos):
                raise TracePatternError(
                    f"expected '->' after '{gap}' at position {pos}"
                )
            pos += 2
            pending_sep = f"-> {gap}"
        else:
            pending_sep = "->"
        expect_step = True

    if expect_step:
        raise TracePatternError(
            "trace pattern ends with a separator (no trailing tool name)"
        )
    return steps


def _compile_regex(steps: list[_Step]) -> re.Pattern[str]:
    """Compile parsed steps into a regex over a comma-joined trace sequence."""
    parts: list[str] = []
    for i, step in enumerate(steps):
        if i == 0:
            # Anchor to the start of an entry: either string start or after a comma.
            parts.append(r"(?:^|,)")
        else:
            sep = step.sep
            if sep == "->":
                parts.append(",")                       # adjacent
            elif sep == "-> *":
                parts.append(r",[^,]+,")                # exactly one between
            elif sep == "-> ...":
                parts.append(r",(?:[^,]+,)+")           # one or more between
            elif sep == "-> ...?":
                parts.append(r",(?:[^,]+,)*")           # zero or more between
            else:
                raise TracePatternError(f"unsupported separator {sep!r}")
        parts.append(re.escape(step.tool))
    parts.append(r"(?=,|$)")                            # right-anchor on entry boundary
    return re.compile("".join(parts))


@functools.lru_cache(maxsize=512)
def compile_trace_pattern(pattern: str) -> Callable[[Iterable[str]], bool]:
    """Compile a trace-pattern expression into a callable matcher.

    The matcher takes an iterable of tool names in chronological order
    (oldest first) and returns True iff the pattern matches anywhere in
    the sequence.
    """
    steps = _tokenize(pattern)
    if any("," in s.tool for s in steps):
        raise TracePatternError(
            "tool names with commas are not allowed in trace patterns"
        )
    regex = _compile_regex(steps)

    def matcher(sequence: Iterable[str]) -> bool:
        joined = ",".join(sequence)
        if not joined:
            return False
        return regex.search(joined) is not None

    matcher.__pattern__ = pattern  # type: ignore[attr-defined]
    matcher.__regex__ = regex      # type: ignore[attr-defined]
    return matcher


def match_trace(pattern: str, sequence: Iterable[str]) -> bool:
    """One-shot convenience helper. Equivalent to ``compile_trace_pattern(p)(seq)``."""
    return compile_trace_pattern(pattern)(sequence)


# ─────────────────────────────────────────────────────────────────────────────
# Named-binding trace matcher  (used by v3 TRACE clause)
# ─────────────────────────────────────────────────────────────────────────────

def match_with_bindings(
    steps: list[tuple[str, str]],       # [(name, sep), ...]  sep="" for first
    trace_rich: list[dict],             # session.trace_rich (oldest-first)
) -> dict[str, dict] | None:
    """Match a v3 TRACE clause against the rich trace and return name→entry bindings.

    Parameters
    ----------
    steps:
        List of ``(placeholder_name, separator)`` pairs exactly as stored in
        ``TraceClause.steps``.  The separator for the first step is always
        ``""``; subsequent separators are one of ``"->"``, ``"-> *"``,
        ``"-> ..."``, ``"-> ...?"``.
    trace_rich:
        Chronological list of rich trace entries (oldest-first).  Each entry
        has at least ``{"tool": str, "args": dict, "result": Any, "ts_ms": int}``
        and optionally ``{"label": dict}``.

    Returns
    -------
    dict mapping placeholder name → trace_rich entry, or ``None`` if no match.
    The **most-recent** (rightmost) match is returned when multiple exist.

    Examples
    --------
    ::

        steps = [("Src", ""), ("Dst", "-> ...?")]
        match_with_bindings(steps, trace_rich)
        # → {"Src": {...}, "Dst": <current-call entry>}
    """
    if not steps or not trace_rich:
        return None

    n = len(trace_rich)
    results: list[dict[str, dict]] = []

    def _backtrack(step_idx: int, entry_idx: int, bindings: dict[str, dict]) -> None:
        """Recursively find all ways to assign placeholder positions."""
        if step_idx == len(steps):
            results.append(dict(bindings))
            return

        name, sep = steps[step_idx]
        if step_idx == 0:
            # First placeholder: try every position from 0 to n-1
            for i in range(n):
                bindings[name] = trace_rich[i]
                _backtrack(step_idx + 1, i + 1, bindings)
        else:
            _, prev_sep = steps[step_idx]  # sep of *this* step relative to previous
            sep = steps[step_idx][1]
            prev_idx = [k for k, v in bindings.items() if v is trace_rich[entry_idx - 1]]
            # entry_idx = position *after* the previous bound entry
            start = entry_idx  # exclusive lower bound (the index of prev+1)

            if sep in ("->", ""):
                # Adjacent: next must be exactly at `start`
                if start < n:
                    bindings[name] = trace_rich[start]
                    _backtrack(step_idx + 1, start + 1, bindings)
            elif sep == "-> *":
                # Exactly one between: exactly start+1
                if start + 1 < n:
                    bindings[name] = trace_rich[start + 1]
                    _backtrack(step_idx + 1, start + 2, bindings)
            elif sep == "-> ...":
                # At least one between: positions start+1, start+2, ...
                for i in range(start + 1, n):
                    bindings[name] = trace_rich[i]
                    _backtrack(step_idx + 1, i + 1, bindings)
            elif sep == "-> ...?":
                # Zero or more between (anywhere after):  start, start+1, ...
                for i in range(start, n):
                    bindings[name] = trace_rich[i]
                    _backtrack(step_idx + 1, i + 1, bindings)
            # Unknown separator → no match

    _backtrack(0, 0, {})
    return results[-1] if results else None
