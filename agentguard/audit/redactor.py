"""Redaction utilities for audit records.

Strips obvious PII / secrets from event content and arguments before they are
persisted to the audit trail, so the trace itself never becomes a data-leak
vector.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"\b(?:\+?\d{1,3}[ -]?)?(?:\d{3}[ -]?){2}\d{4}\b")),
    ("api_key", re.compile(r"\b(?:sk|pk|api|key|token)[-_][A-Za-z0-9]{12,}\b", re.I)),
]

_SECRET_KEYS = {"password", "passwd", "secret", "token", "api_key", "apikey", "authorization"}


class Redactor:
    """Replaces sensitive substrings with ``[REDACTED:<kind>]`` markers."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def redact_text(self, text: str | None) -> str | None:
        if not self.enabled or not text:
            return text
        out = text
        for kind, pattern in _PATTERNS:
            out = pattern.sub(f"[REDACTED:{kind}]", out)
        return out

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return args
        out: dict[str, Any] = {}
        for key, value in args.items():
            if key.lower() in _SECRET_KEYS:
                out[key] = "[REDACTED:secret]"
            elif isinstance(value, str):
                out[key] = self.redact_text(value)
            elif isinstance(value, dict):
                out[key] = self.redact_args(value)
            else:
                out[key] = value
        return out
