"""Flags low-confidence LLM reasoning so the PEP can escalate (ask_user)."""

from __future__ import annotations

import re

from agentguard.middleware.base import Middleware
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.schemas.risk import RiskAssessment

_UNCERTAIN_MARKERS = [
    re.compile(r"\bi'?m not sure\b", re.I),
    re.compile(r"\bnot certain\b", re.I),
    re.compile(r"\bi (think|guess|assume)\b", re.I),
    re.compile(r"\bmight be\b", re.I),
    re.compile(r"\bprobably\b", re.I),
    re.compile(r"\bunclear\b", re.I),
]


class UncertaintyDetector(Middleware):
    name = "uncertainty"

    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        if event.type not in (EventType.LLM_THOUGHT, EventType.FINAL_RESPONSE):
            return event
        text = event.content or ""
        markers = [p.pattern for p in _UNCERTAIN_MARKERS if p.search(text)]
        # Explicit confidence signal from the adapter wins if present.
        confidence = event.metadata.get("confidence")
        is_uncertain = bool(markers) or (
            isinstance(confidence, (int, float)) and confidence < 0.5
        )
        if is_uncertain:
            event.annotate("uncertain", markers or [f"confidence={confidence}"])
            risk.add("uncertainty", 0.4, markers=markers, confidence=confidence)
        return event
