"""PolicyActor: rule evaluation (Instruction.md §3.2).

Receives events + features from SessionActor, evaluates compiled rules,
and forwards candidate outcomes to DecisionActor.
"""

from __future__ import annotations

from typing import Any, Iterable

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.policy.evaluator.matcher import FastEvaluator
from agentguard.runtime.actors.base import BaseActor
from agentguard.runtime.event_bus import EventBus, Message


class PolicyActor(BaseActor):
    actor_name = "policy"

    def __init__(
        self,
        bus: EventBus,
        rules: Iterable[CompiledRule] | None = None,
        *,
        rule_version: str = "v1",
        router: Any = None,
    ) -> None:
        super().__init__(bus)
        self._evaluator = FastEvaluator(rules, rule_version=rule_version, router=router)

    def load(self, rules: Iterable[CompiledRule]) -> None:
        self._evaluator.load(rules)

    @property
    def evaluator(self) -> FastEvaluator:
        return self._evaluator

    async def handle(self, msg: Message) -> None:
        if msg.topic == "evaluate_policy":
            event: RuntimeEvent = msg.payload["event"]
            features: dict[str, Any] = msg.payload.get("features", {})
            decision = self._evaluator.evaluate(event, features)

            decision_msg = Message(
                topic="make_decision",
                payload={"event": event, "decision": decision},
                reply_to=msg.reply_to,
                sender=self.actor_name,
            )
            await self.bus.publish(decision_msg)

    async def on_start(self) -> None:
        self.bus.subscribe("evaluate_policy", self.receive)
