"""Token-bucket rate limiter keyed by (session, tool).

Annotates ``rate_limited`` when a caller exceeds its budget so policy rules can
deny or degrade. Kept in-process and dependency-free.
"""

from __future__ import annotations

import threading
import time

from agentguard.middleware.base import Middleware
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.schemas.risk import RiskAssessment


class RateLimiter(Middleware):
    name = "rate_limiter"

    def __init__(self, *, capacity: int = 30, refill_per_sec: float = 5.0) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()

    def _take(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True

    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        if event.type not in (
            EventType.TOOL_CALL,
            EventType.NETWORK_ACTION,
            EventType.FILE_OP,
        ):
            return event
        key = f"{event.session_id}:{event.tool_name or event.type.value}"
        if not self._take(key):
            event.annotate("rate_limited", True)
            risk.add("rate_limit", 0.5, key=key)
        return event
