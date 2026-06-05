"""Time helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ms() -> int:
    """Current wall-clock time in milliseconds since the epoch."""
    return int(time.time() * 1000)


def iso_now() -> str:
    """Current UTC time as an ISO-8601 string with a trailing ``Z``."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
