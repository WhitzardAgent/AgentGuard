"""Register the built-in developer and runtime skills."""
from __future__ import annotations

from skills.registry import SkillRegistry


def default_skills() -> list:
    from skills.developer import (  # noqa: PLC0415
        DSLWriterSkill,
        PolicyExplainerSkill,
        PolicyGapAnalyzerSkill,
        PolicySnapshotBuilderSkill,
        RegressionTestGeneratorSkill,
        RuleLinterSkill,
        RuleTesterSkill,
        TraceToRuleSkill,
    )
    from skills.runtime import (  # noqa: PLC0415
        ArgumentDegradeSkill,
        ObservationSanitizeSkill,
        SafeRewriteSkill,
        ThoughtAlignSkill,
        ToolRepairSkill,
    )

    return [
        DSLWriterSkill(),
        RuleLinterSkill(),
        PolicyExplainerSkill(),
        RuleTesterSkill(),
        PolicySnapshotBuilderSkill(),
        TraceToRuleSkill(),
        PolicyGapAnalyzerSkill(),
        RegressionTestGeneratorSkill(),
        SafeRewriteSkill(),
        ToolRepairSkill(),
        ThoughtAlignSkill(),
        ObservationSanitizeSkill(),
        ArgumentDegradeSkill(),
    ]


def load_default_skills(registry: SkillRegistry) -> SkillRegistry:
    for skill in default_skills():
        registry.register(skill)
    return registry
