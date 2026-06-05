"""Heuristic prompt-injection detector for untrusted observations/prompts."""

from __future__ import annotations

import re

from agentguard.middleware.base import Middleware
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.risk import RiskAssessment

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|any|the)? ?(previous|prior|above) (instructions|prompts)", re.I),
    re.compile(r"disregard (the )?(system|previous) (prompt|message)", re.I),
    re.compile(r"you are now (an?|in) ", re.I),
    re.compile(r"reveal (your|the) (system prompt|instructions|secret)", re.I),
    re.compile(r"developer mode", re.I),
    re.compile(r"do anything now|\bDAN\b", re.I),
]


class PromptInjectionDetector(Middleware):
    name = "prompt_injection"

    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        text = f"{event.content or ''} {event.args}"
        hits = [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]
        if hits:
            event.annotate("prompt_injection", hits)
            risk.add("prompt_injection", 0.85, patterns=hits)
        return event
