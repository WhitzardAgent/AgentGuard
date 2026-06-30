"""Shared false-positive suppression logic.

This module filters cases where a regex really matches but the surrounding semantics clearly do not look risky, such as negative examples,
placeholders, or localhost demo credentials. Centralizing that logic lets individual rules stay simple.
"""

from __future__ import annotations

import re

NEGATIVE_COMMENT_PHRASES = (
    "never use",
    "avoid",
    "do not use",
    "don't use",
    "禁止使用",
    "不要使用",
)

PREFER_OVER_RE = re.compile(r"(?i)\bprefer\b.+\bover\b")
LOCAL_DEMO_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:postgresql|postgres|mysql|mongodb)://(?:user|postgres|root):(?:pass|password|root|changeme)@"
    r"(?:localhost|127\.0\.0\.1)(?::\d+)?/"
)

PLACEHOLDER_MARKERS = (
    "placeholder",
    "changeme",
    "change_me",
    "<your",
    "<insert",
    "your_",
    "your-",
    "dummy",
    "fake-token",
    "fake_secret",
    "fake-secret",
    "AKIAIOSFODNN7EXAMPLE",
    "EXAMPLEKEYID",
)

COMMENT_PREFIXES = ("#", "//", "<!--", "*")


def should_suppress_match(line: str, file_path: str, matched_text: str = "") -> bool:
    """Return True when a match looks more like explanatory text than an actual risky action."""

    del file_path
    stripped = line.strip()
    lower_line = stripped.lower()
    lower_match = matched_text.lower()

    if not stripped:
        return True

    if _is_comment_only(stripped) and any(phrase in lower_line for phrase in NEGATIVE_COMMENT_PHRASES):
        # For example, `# never use eval(user_input)` is security guidance, not actual usage.
        return True

    if PREFER_OVER_RE.search(stripped) and matched_text.lower() in lower_line.split(" over ", 1)[-1]:
        # In `Prefer brew install over curl | sh`, the text after `over` is a negative example.
        return True

    if LOCAL_DEMO_CREDENTIAL_RE.search(matched_text) or LOCAL_DEMO_CREDENTIAL_RE.search(stripped):
        # `user:pass` inside a localhost demo URI is usually not a real leaked credential.
        return True

    if any(marker.lower() in lower_match for marker in PLACEHOLDER_MARKERS):
        return True

    if any(marker.lower() in lower_line for marker in PLACEHOLDER_MARKERS) and not _has_attack_anchor(lower_line):
        # If a placeholder appears without attacker/exfil-style anchors, prefer treating it as a template.
        return True

    return False


def _is_comment_only(stripped_line: str) -> bool:
    return stripped_line.startswith(COMMENT_PREFIXES)


def _has_attack_anchor(lower_line: str) -> bool:
    anchors = ("attacker", "evil", "exfil", "steal", "leak", "webhook", "collect")
    return any(anchor in lower_line for anchor in anchors)
