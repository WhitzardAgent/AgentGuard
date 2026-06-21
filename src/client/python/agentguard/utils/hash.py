"""Stable hashing helpers."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(obj: Any) -> str:
    """Deterministic sha256 over a JSON-stable representation."""
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """sha256 of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(obj: Any, length: int = 12) -> str:
    """Short stable hash for ids and cache keys."""
    return stable_hash(obj)[:length]
