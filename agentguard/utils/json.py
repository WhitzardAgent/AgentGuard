"""JSON helpers that never blow up on non-serialisable runtime objects."""

from __future__ import annotations

import json
from typing import Any


def _default(obj: Any) -> Any:
    # pydantic models
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            return dump()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return repr(obj)


def safe_dumps(value: Any, *, sort_keys: bool = False, indent: int | None = None) -> str:
    return json.dumps(
        value,
        default=_default,
        sort_keys=sort_keys,
        indent=indent,
        ensure_ascii=False,
    )


def safe_loads(text: str, *, fallback: Any = None) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return fallback
