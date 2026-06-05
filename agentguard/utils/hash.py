"""Stable hashing helpers (used for decision-cache keys and content ids)."""

from __future__ import annotations

import hashlib
from typing import Any

from agentguard.utils.json import safe_dumps


def stable_hash(value: Any, *, length: int = 16) -> str:
    """Deterministic short hash of any JSON-serialisable value.

    Dict key ordering is normalised so semantically-equal inputs hash equally.
    """
    payload = safe_dumps(value, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:length]


def content_hash(text: str, *, length: int = 32) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:length]
