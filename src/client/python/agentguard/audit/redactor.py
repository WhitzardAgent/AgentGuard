"""Audit redaction: strip secrets before persisting records."""
from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_key",
    "private_key",
    "credit_card",
    "card_number",
)
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),  # card-like
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),
]
REDACTED = "[REDACTED]"


def redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact secrets from arbitrary structures."""
    if key and any(h in key.lower() for h in _SECRET_KEY_HINTS):
        return REDACTED
    if isinstance(value, str):
        out = value
        for pat in _PATTERNS:
            out = pat.sub(REDACTED, out)
        return out
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value
