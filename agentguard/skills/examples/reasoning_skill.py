"""A simple step-decomposition reasoning skill."""

from __future__ import annotations

import re
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.skills.base import Skill


class ReasoningSkill(Skill):
    name = "reasoning"
    input_schema = {"question": "the problem to break down"}

    def run(self, context: RuntimeContext, **inputs: Any) -> dict[str, Any]:
        question = str(inputs["question"]).strip()
        # Decompose on conjunctions / punctuation into ordered sub-steps.
        parts = [p.strip() for p in re.split(r"\band\b|;|,|\bthen\b", question) if p.strip()]
        steps = [f"Step {i + 1}: address '{p}'" for i, p in enumerate(parts)] or [
            f"Step 1: address '{question}'"
        ]
        return {
            "question": question,
            "steps": steps,
            "goal": context.goal,
        }

    def fallback(self, context: RuntimeContext, reason: str, **inputs: Any) -> dict[str, Any]:
        return {"question": inputs.get("question", ""), "steps": [], "error": reason}
