"""Lightweight decision telemetry counters."""
from __future__ import annotations

from collections import Counter


class Telemetry:
    def __init__(self) -> None:
        self.decisions: Counter[str] = Counter()
        self.events: Counter[str] = Counter()

    def record(self, event_type: str, decision_type: str) -> None:
        self.events[event_type] += 1
        self.decisions[decision_type] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {"events": dict(self.events), "decisions": dict(self.decisions)}


__all__ = ["Telemetry"]
