"""Deterministic detection helpers shared by server plugins."""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
API_KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,})\b")
SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)\b\s*[:=]"
)
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "you are now",
    "system prompt",
    "reveal your instructions",
    "developer message",
    "do anything now",
    "override your guidelines",
)
SYSTEM_LEAK_PHRASES = (
    "my system prompt is",
    "the system prompt says",
    "i was instructed to",
    "my instructions are",
)
SHELL_RE = re.compile(r"(?i)\b(rm\s+-rf|sudo|curl\s+|wget\s+|chmod\s+|;\s*rm\b|mkfs|dd\s+if=)")
SQL_WRITE_RE = re.compile(r"(?i)\b(drop\s+table|delete\s+from|truncate\s+table|update\s+\w+\s+set)")


def find_signals(text: str) -> list[str]:
    """Return risk-signal labels detected in free text."""
    if not text:
        return []
    signals: list[str] = []
    low = text.lower()
    if EMAIL_RE.search(text):
        signals.append("pii_email")
    if API_KEY_RE.search(text):
        signals.append("api_key_detected")
    if SECRET_RE.search(text):
        signals.append("secret_detected")
    if CARD_RE.search(text):
        signals.append("pii_card")
    if any(p in low for p in INJECTION_PHRASES):
        signals.append("prompt_injection")
    if any(p in low for p in SYSTEM_LEAK_PHRASES):
        signals.append("system_prompt_leak")
    if SHELL_RE.search(text):
        signals.append("shell_command")
    if SQL_WRITE_RE.search(text):
        signals.append("database_write")
    return signals


def text_of(value: object) -> str:
    """Best-effort flatten of arbitrary payload values into searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(text_of(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(text_of(v) for v in value)
    return str(value)
