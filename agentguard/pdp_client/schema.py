"""Wire schema exchanged with the PDP service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision, DecisionAction
from agentguard.schemas.events import RuntimeEvent


class PDPRequest(BaseModel):
    event: RuntimeEvent
    context: RuntimeContext
    annotations: dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PDPResponse(BaseModel):
    action: DecisionAction = DecisionAction.ALLOW
    reason: str = ""
    risk_score: float = 0.0
    matched_rules: list[str] = Field(default_factory=list)
    obligations: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PDPResponse":
        return cls.model_validate(payload)

    def to_decision(self) -> Decision:
        from agentguard.schemas.decision import Obligation

        return Decision(
            action=self.action,
            reason=self.reason or "pdp_decision",
            risk_score=self.risk_score,
            matched_rules=list(self.matched_rules),
            obligations=[Obligation(**o) for o in self.obligations],
            source="pdp",
        )
