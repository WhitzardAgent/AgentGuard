"""Aggregates upstream signals into a final risk score + category list.

Runs last in the default chain so it can read annotations left by the other
analyzers and fold in coarse capability-based risk.
"""

from __future__ import annotations

from agentguard.middleware.base import Middleware
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import RuntimeEvent
from agentguard.schemas.risk import RiskAssessment

_CAPABILITY_RISK = {
    "shell": 0.7,
    "network": 0.4,
    "filesystem": 0.4,
    "exec": 0.8,
    "delete": 0.6,
}


class RiskClassifier(Middleware):
    name = "risk_classifier"

    def process(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        risk: RiskAssessment,
    ) -> RuntimeEvent:
        for cap in event.capabilities:
            weight = _CAPABILITY_RISK.get(cap)
            if weight:
                risk.add(f"capability:{cap}", weight)
        # Surface the rolled-up assessment for downstream consumers/audit.
        event.annotations["risk_categories"] = list(dict.fromkeys(risk.categories))
        event.annotations["risk_score"] = risk.score
        event.annotations["risk_level"] = risk.level.value
        return event
