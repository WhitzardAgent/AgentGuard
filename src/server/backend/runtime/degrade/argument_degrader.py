"""Argument-level degradation."""
from __future__ import annotations

from typing import Any

_SINK_KEYS = ("to", "recipient", "url", "endpoint", "host", "channel", "command")


def degrade_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    out = dict(arguments)
    removed = []
    for key in _SINK_KEYS:
        if key in out:
            out[key] = None
            removed.append(key)
    out["_mode"] = "draft"
    return {"arguments": out, "removed": removed}
