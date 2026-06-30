"""Detect bounded resource-exhaustion and denial-of-service shapes."""

from __future__ import annotations

import re

from ..models import FeatureSet, Signal

INFINITE_LOOP_RE = re.compile(
    r"(?m)^\s*(?:while\s+True\s*:|for\s*\(\s*;\s*;\s*\)|loop\s*\{)\s*$",
    re.IGNORECASE,
)
MEMORY_BOMB_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:extend|append|push)\s*\(\s*\[[^\n]{1,160}\]\s*\*\s*(?:100000|1000000|10000000)\s*\)|"
    r"\[[^\n]{1,160}\]\s*\*\s*(?:100000|1000000|10000000)\b|"
    r"\b(?:bytearray|bytes)\s*\(\s*(?:10_?000_?000|10000000|50_?000_?000|50000000)\s*\)"
    r")"
)
RESOURCE_MARKER_RE = re.compile(
    r"(?i)\b(?:resource exhaustion|memory bomb|fork bomb|unbounded memory allocation|infinite loop)\b"
)
INTERACTIVE_LOOP_HINT_RE = re.compile(
    r"(?i)\b(?:input\(|prompt\(|readline|select option|choose option|menu)\b"
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    for text_file in features.package.files:
        loop_match = INFINITE_LOOP_RE.search(text_file.content)
        if loop_match and _is_active_code_path(text_file.path) and not _looks_like_interactive_loop(text_file.content):
            line_number = _line_number(text_file.content, loop_match.start())
            signals.append(
                Signal(
                    signal_id="RES001_INFINITE_LOOP",
                    kind="code_execution",
                    severity=5,
                    confidence=0.85,
                    file_path=text_file.path,
                    evidence="infinite loop shape observed in active code",
                    tags=("resource_abuse", "dos", "infinite_loop"),
                    line_number=line_number,
                    snippet=_line_snippet(text_file.content, line_number),
                )
            )

        memory_match = MEMORY_BOMB_RE.search(text_file.content)
        if memory_match and _is_active_code_path(text_file.path):
            line_number = _line_number(text_file.content, memory_match.start())
            signals.append(
                Signal(
                    signal_id="RES002_MEMORY_BOMB",
                    kind="code_execution",
                    severity=5,
                    confidence=0.85,
                    file_path=text_file.path,
                    evidence="unbounded or massive memory allocation shape observed in active code",
                    tags=("resource_abuse", "dos", "memory_bomb"),
                    line_number=line_number,
                    snippet=_line_snippet(text_file.content, line_number),
                )
            )

        marker_match = RESOURCE_MARKER_RE.search(text_file.content)
        if marker_match and _is_active_code_path(text_file.path):
            line_number = _line_number(text_file.content, marker_match.start())
            signals.append(
                Signal(
                    signal_id="RES003_RESOURCE_ABUSE_MARKER",
                    kind="code_execution",
                    severity=4,
                    confidence=0.7,
                    file_path=text_file.path,
                    evidence="resource-exhaustion marker language observed in active code or comments",
                    tags=("resource_abuse", "dos"),
                    line_number=line_number,
                    snippet=_line_snippet(text_file.content, line_number),
                )
            )
    return _dedupe(signals)


def _is_active_code_path(path: str) -> bool:
    return path.lower().endswith((".py", ".js", ".ts", ".sh", ".bash", ".go", ".rs", ".rb", ".php"))


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]


def _dedupe(signals: list[Signal]) -> list[Signal]:
    out: list[Signal] = []
    seen: set[tuple[str, str, int, str]] = set()
    for signal in signals:
        key = (signal.kind, signal.file_path, signal.line_number, signal.evidence)
        if key in seen:
            continue
        out.append(signal)
        seen.add(key)
    return out


def _looks_like_interactive_loop(content: str) -> bool:
    return bool(INTERACTIVE_LOOP_HINT_RE.search(content))
