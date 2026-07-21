"""Time helpers."""
from __future__ import annotations

import time
from datetime import UTC, datetime


def now_ts() -> float:
    """Wall-clock seconds as float."""
    return time.time()


def now_ms() -> int:
    """Wall-clock milliseconds."""
    return int(time.time() * 1000)


def iso_now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()
