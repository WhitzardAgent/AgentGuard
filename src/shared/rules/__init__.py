"""Shared rule loading, matching and snapshot helpers."""
from __future__ import annotations

from shared.rules.builtin import builtin_rules
from shared.rules.llm_dsl_generator import (
    LLMRuleGeneratorWorkflow,
    RuleCandidate,
    RuleGenerationRequest,
    RuleGenerationSession,
    RuleValidationResult,
    ValidationIssue,
    load_generation_template,
)
from shared.rules.loader import load_policy, load_rules_dir, load_rules_file
from shared.rules.matcher import MatchResult, match_rules
from shared.rules.snapshot import PolicySnapshot

__all__ = [
    "builtin_rules",
    "LLMRuleGeneratorWorkflow",
    "RuleCandidate",
    "RuleGenerationRequest",
    "RuleGenerationSession",
    "RuleValidationResult",
    "ValidationIssue",
    "load_generation_template",
    "load_policy",
    "load_rules_dir",
    "load_rules_file",
    "MatchResult",
    "match_rules",
    "PolicySnapshot",
]
