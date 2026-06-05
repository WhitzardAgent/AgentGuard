"""Middleware base class and chain runner."""

from __future__ import annotations

from abc import ABC, abstractmethod

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.risk import RiskAssessment


class Middleware(ABC):
    """Analyzes an event, annotating it and contributing risk signals."""

    name: str = "middleware"

    @abstractmethod
    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        """Return the (possibly annotated) event. Must not raise on bad input."""
        raise NotImplementedError


class MiddlewareChain:
    """Runs a list of middleware in order, accumulating annotations + risk."""

    def __init__(self, middleware: list[Middleware] | None = None) -> None:
        self._middleware: list[Middleware] = list(middleware or [])

    def add(self, middleware: Middleware) -> None:
        self._middleware.append(middleware)

    @property
    def middleware(self) -> list[Middleware]:
        return list(self._middleware)

    def run(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
    ) -> tuple[RuntimeEvent, RiskAssessment]:
        risk = RiskAssessment()
        current = event
        for mw in self._middleware:
            try:
                current = mw.process(current, context, risk)
            except Exception:
                # An analyzer failure degrades to "no signal", never a crash.
                continue
        current.annotations["risk_score"] = risk.score
        current.annotations["risk_level"] = risk.level.value
        return current, risk
