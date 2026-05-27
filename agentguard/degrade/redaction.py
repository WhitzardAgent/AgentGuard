"""Output redaction utilities for sensitive field masking."""

from __future__ import annotations

import re
from typing import Any


_SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),          # SSN
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # email
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),  # credit card
]


def redact_string(text: str, replacement: str = "[REDACTED]") -> str:
    """Replace known sensitive patterns in text."""
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub(replacement, text)
    return text


def redact_fields(data: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    """Replace specified field values with [REDACTED]."""
    result = dict(data)
    for f in fields:
        if f in result:
            result[f] = "[REDACTED]"
    return result
