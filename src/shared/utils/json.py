"""Robust JSON helpers that never raise on serialization."""
from __future__ import annotations

import json
from typing import Any


def safe_dumps(obj: Any, *, indent: int | None = None) -> str:
    """Serialize to JSON, falling back to str() for unknown types."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str, indent=indent)
    except (TypeError, ValueError):
        return json.dumps(str(obj), ensure_ascii=False)


def safe_loads(raw: str | bytes | None, fallback: Any = None) -> Any:
    """Parse JSON, returning a fallback on failure."""
    if raw is None:
        return fallback
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback
