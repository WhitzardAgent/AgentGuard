"""Skills — reusable, policy-aware reasoning modules.

A Skill abstracts a syntax/semantics pattern into a callable unit with an input
schema, reasoning logic and a fallback/degrade path. Skills are registered with
:class:`~agentguard.AgentGuard` and can be invoked by the Harness or directly.
"""

from agentguard.skills.base import Skill, SkillResult, SkillRegistry

__all__ = ["Skill", "SkillResult", "SkillRegistry"]
