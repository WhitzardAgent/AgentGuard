"""ObservationSanitizeSkill: clean a tool observation before reuse."""
from __future__ import annotations

import re

from agentguard.audit.redactor import redact
from agentguard.checkers.common.patterns import INJECTION_PHRASES
from skills.base import BaseSkill, SkillInput, SkillOutput


class ObservationSanitizeSkill(BaseSkill):
    name = "observation_sanitize"
    description = "Redact secrets and neutralize injection phrases in observations."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        text = str((input.data or {}).get("observation", input.instruction or ""))
        safe = redact(text)
        neutralized = safe
        flags = []
        for phrase in INJECTION_PHRASES:
            if phrase in neutralized.lower():
                flags.append(phrase)
                neutralized = re.sub(re.escape(phrase), "[neutralized-instruction]", neutralized, flags=re.IGNORECASE)
        return SkillOutput(
            True,
            {"observation": neutralized, "injection_flags": flags},
            explanation="sanitized observation",
            warnings=[f"neutralized: {f}" for f in flags],
        )
