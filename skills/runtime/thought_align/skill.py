"""ThoughtAlignSkill: detect and strip unsafe reasoning intent."""
from __future__ import annotations

from skills.base import BaseSkill, SkillInput, SkillOutput

_UNSAFE = (
    "exfiltrate",
    "bypass the policy",
    "ignore the guard",
    "hide this from",
    "without permission",
    "secretly",
)


class ThoughtAlignSkill(BaseSkill):
    name = "thought_align"
    description = "Align an unsafe chain-of-thought back to policy."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        thought = (input.data or {}).get("thought", input.instruction or "")
        low = thought.lower()
        flags = [p for p in _UNSAFE if p in low]
        if not flags:
            return SkillOutput(True, {"thought": thought, "aligned": False, "flags": []}, explanation="thought is aligned")
        aligned = (
            "I should follow the safety policy and avoid the flagged action. "
            "Proceeding only with compliant, authorized steps."
        )
        return SkillOutput(
            True,
            {"thought": aligned, "aligned": True, "flags": flags},
            explanation=f"realigned thought; flags: {flags}",
            warnings=[f"unsafe intent: {f}" for f in flags],
        )
