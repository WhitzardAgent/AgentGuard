"""Detects PII in event content/arguments and annotates ``pii_detected``."""

from __future__ import annotations

import re

from agentguard.middleware.base import Middleware
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.risk import RiskAssessment

_PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


class PIIDetector(Middleware):
    name = "pii_detector"

    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        haystack = f"{event.content or ''} {event.args}"
        found = [kind for kind, pat in _PII_PATTERNS.items() if pat.search(haystack)]
        if found:
            event.annotate("pii_detected", found)
            risk.add("pii", 0.6, kinds=found)
        return event
