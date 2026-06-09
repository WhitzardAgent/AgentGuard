"""Data sensitivity labels."""
from __future__ import annotations

SENSITIVITY_LEVELS = ("public", "internal", "confidential", "secret")

_SECRET_SIGNALS = {"secret_detected", "api_key_detected"}
_PII_SIGNALS = {"pii_email", "pii_card", "pii_detected"}


def sensitivity_from_signals(signals: list[str]) -> str:
    s = set(signals)
    if s & _SECRET_SIGNALS:
        return "secret"
    if s & _PII_SIGNALS:
        return "confidential"
    return "internal"
