"""Small regex-rule runner.

All `RuleSpec`-based rules pass through this module, which centralizes:
- single-line and multi-line matching;
- false-positive suppression。
- per-rule match caps;
- the positions and snippets needed for evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

from .context_filters import should_suppress_match
from .models import FeatureHit, TextFile

_CHAR_CLASS_RE = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class RuleSpec:
    """A reusable text-matching rule."""

    rule_id: str
    kind: str
    patterns: tuple[Pattern[str], ...]
    evidence: str
    tags: tuple[str, ...] = ()
    exclude_patterns: tuple[Pattern[str], ...] = ()
    multiline: bool = False
    max_matches: int = 20


def rx(pattern: str, flags: int = 0) -> Pattern[str]:
    return re.compile(pattern, flags)


def find_rule_hits(rule: RuleSpec, text_file: TextFile) -> list[FeatureHit]:
    hits: list[FeatureHit] = []
    lines = text_file.content.splitlines()

    for pattern in rule.patterns:
        # Rules such as private-key blocks require multi-line matching; ordinary rules stay line-based to preserve exact line numbers.
        if rule.multiline or _is_multiline_pattern(pattern):
            _extend_multiline_hits(hits, rule, text_file, pattern, lines)
        else:
            _extend_line_hits(hits, rule, text_file, pattern, lines)

        if len(hits) >= rule.max_matches:
            return hits[: rule.max_matches]

    return hits


def _extend_line_hits(
    hits: list[FeatureHit],
    rule: RuleSpec,
    text_file: TextFile,
    pattern: Pattern[str],
    lines: list[str],
) -> None:
    for line_number, line in enumerate(lines, start=1):
        if _excluded(rule, line):
            continue
        for match in pattern.finditer(line):
            matched_text = match.group(0)
            if should_suppress_match(line, text_file.path, matched_text):
                # Filter placeholders, negative examples, and local demo credentials in one place to avoid duplicated logic in individual rules.
                continue
            hits.append(_hit(rule, text_file, matched_text, line_number, line))
            if len(hits) >= rule.max_matches:
                return


def _extend_multiline_hits(
    hits: list[FeatureHit],
    rule: RuleSpec,
    text_file: TextFile,
    pattern: Pattern[str],
    lines: list[str],
) -> None:
    content = text_file.content
    for match in pattern.finditer(content):
        matched_text = match.group(0)
        if _excluded(rule, matched_text):
            continue
        line_number = content.count("\n", 0, match.start()) + 1
        line = lines[line_number - 1] if 0 <= line_number - 1 < len(lines) else matched_text
        if should_suppress_match(line, text_file.path, matched_text):
            continue
        hits.append(_hit(rule, text_file, matched_text, line_number, line))
        if len(hits) >= rule.max_matches:
            return


def _hit(rule: RuleSpec, text_file: TextFile, matched_text: str, line_number: int, line: str) -> FeatureHit:
    value = matched_text.strip()
    return FeatureHit(
        rule_id=rule.rule_id,
        kind=rule.kind,
        value=value[:120],
        file_path=text_file.path,
        evidence=rule.evidence,
        tags=rule.tags,
        line_number=line_number,
        # Truncate `matched_text`/`value` so evidence stays bounded and does not expose full large payloads.
        matched_text=value[:120],
        snippet=line.strip()[:200],
    )


def _excluded(rule: RuleSpec, text: str) -> bool:
    return any(pattern.search(text) for pattern in rule.exclude_patterns)


def _is_multiline_pattern(pattern: Pattern[str]) -> bool:
    # Strip character classes before checking for `\n` so common single-line constructs such as `[^\n]` are not misclassified as multi-line rules.
    stripped = _CHAR_CLASS_RE.sub("", pattern.pattern)
    return "\\n" in stripped
