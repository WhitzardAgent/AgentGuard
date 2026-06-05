"""Retry policy with exponential backoff for transient PDP failures."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.2
    max_delay: float = 2.0
    backoff: float = 2.0

    def run(self, fn: Callable[[], T]) -> T:
        """Invoke ``fn`` retrying on exception with exponential backoff.

        Re-raises the last exception when all attempts are exhausted.
        """
        delay = self.base_delay
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - we re-raise after loop
                last_exc = exc
                if attempt >= self.max_attempts:
                    break
                time.sleep(min(delay, self.max_delay))
                delay *= self.backoff
        assert last_exc is not None
        raise last_exc
