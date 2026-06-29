"""Tool-call trace pattern parsing and matching.

This restores the v1.0 TRACE semantics for tool-call-only chains:

    A -> B
    A -> * -> B
    A -> ... -> B
    A -> ...? -> B
"""
from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class TracePatternError(ValueError):
    """Raised when a TRACE clause cannot be parsed."""


@dataclass(frozen=True)
class TraceStep:
    name: str
    sep: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "sep": self.sep}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceStep":
        return cls(
            name=str(data.get("name") or "").strip(),
            sep=str(data.get("sep") or ""),
        )


def parse_trace_pattern(pattern: str) -> list[TraceStep]:
    """Parse a TRACE path into steps with separators."""
    text = str(pattern or "").strip()
    if not text:
        raise TracePatternError("empty trace pattern")

    steps: list[TraceStep] = []
    pos = 0
    n = len(text)
    pending_sep = ""
    expect_step = True
    while pos < n:
        if text[pos].isspace():
            pos += 1
            continue
        if expect_step:
            match = re.match(r"[A-Za-z_][\w-]*", text[pos:])
            if not match:
                raise TracePatternError(
                    f"expected placeholder at position {pos}: {text[pos:pos + 16]!r}"
                )
            steps.append(TraceStep(name=match.group(0), sep=pending_sep))
            pending_sep = ""
            pos += match.end()
            expect_step = False
            continue
        if not text.startswith("->", pos):
            raise TracePatternError(
                f"expected '->' at position {pos}: {text[pos:pos + 16]!r}"
            )
        pos += 2
        while pos < n and text[pos].isspace():
            pos += 1
        gap = ""
        if text.startswith("...?", pos):
            gap = "...?"
            pos += 4
        elif text.startswith("...", pos):
            gap = "..."
            pos += 3
        elif pos < n and text[pos] == "*":
            gap = "*"
            pos += 1
        if gap:
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
        raise TracePatternError("trace pattern ends with a separator")
    return steps


def trace_steps_to_pattern(steps: list[TraceStep]) -> str:
    if not steps:
        return ""
    parts = [steps[0].name]
    for step in steps[1:]:
        parts.append(step.sep or "->")
        parts.append(step.name)
    return " ".join(parts)


def _compile_regex(steps: list[TraceStep]) -> re.Pattern[str]:
    parts: list[str] = []
    for index, step in enumerate(steps):
        if index == 0:
            parts.append(r"(?:^|,)")
        else:
            if step.sep == "->":
                parts.append(",")
            elif step.sep == "-> *":
                parts.append(r",[^,]+,")
            elif step.sep == "-> ...":
                parts.append(r",(?:[^,]+,)+")
            elif step.sep == "-> ...?":
                parts.append(r",(?:[^,]+,)*")
            else:
                raise TracePatternError(f"unsupported separator {step.sep!r}")
        parts.append(re.escape(step.name))
    parts.append(r"(?=,|$)")
    return re.compile("".join(parts))


@functools.lru_cache(maxsize=512)
def compile_trace_pattern(pattern: str):
    steps = parse_trace_pattern(pattern)
    regex = _compile_regex(steps)

    def matcher(sequence: Iterable[str]) -> bool:
        joined = ",".join(sequence)
        if not joined:
            return False
        return regex.search(joined) is not None

    return matcher


def match_trace(pattern: str, sequence: Iterable[str]) -> bool:
    return compile_trace_pattern(pattern)(sequence)


def match_with_bindings(
    steps: list[TraceStep],
    trace_entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    """Return placeholder bindings for the most recent matching chain."""
    if not steps or not trace_entries:
        return None

    n = len(trace_entries)
    results: list[dict[str, dict[str, Any]]] = []

    def backtrack(step_idx: int, prev_idx: int, bindings: dict[str, dict[str, Any]]) -> None:
        if step_idx == len(steps):
            results.append(dict(bindings))
            return

        step = steps[step_idx]
        if step_idx == 0:
            for idx in range(n):
                bindings[step.name] = trace_entries[idx]
                backtrack(step_idx + 1, idx, bindings)
            bindings.pop(step.name, None)
            return

        candidates: range
        if step.sep == "->":
            candidates = range(prev_idx + 1, min(prev_idx + 2, n))
        elif step.sep == "-> *":
            candidates = range(prev_idx + 2, min(prev_idx + 3, n))
        elif step.sep == "-> ...":
            candidates = range(prev_idx + 2, n)
        elif step.sep == "-> ...?":
            candidates = range(prev_idx + 1, n)
        else:
            return

        for idx in candidates:
            bindings[step.name] = trace_entries[idx]
            backtrack(step_idx + 1, idx, bindings)
        bindings.pop(step.name, None)

    backtrack(0, -1, {})
    return results[-1] if results else None
