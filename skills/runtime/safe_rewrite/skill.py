"""SafeRewriteSkill: redact secrets/PII from text."""
from __future__ import annotations

from agentguard.audit.redactor import redact
from skills.base import BaseSkill, SkillInput, SkillOutput


class SafeRewriteSkill(BaseSkill):
    name = "safe_rewrite"
    description = "Rewrite text with secrets and PII redacted."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        text = (input.data or {}).get("text", input.instruction or "")
        safe = redact(text)
        changed = safe != text
        return SkillOutput(
            True,
            {"text": safe, "changed": changed},
            explanation="redacted sensitive content" if changed else "no changes needed",
        )
