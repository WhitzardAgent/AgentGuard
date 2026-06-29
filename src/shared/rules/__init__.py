"""Shared rule loading, matching and snapshot helpers."""
from __future__ import annotations

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


def __getattr__(name: str):
    if name == "builtin_rules":
        from shared.rules.builtin import builtin_rules

        return builtin_rules
    if name in {
        "LLMRuleGeneratorWorkflow",
        "RuleCandidate",
        "RuleGenerationRequest",
        "RuleGenerationSession",
        "RuleValidationResult",
        "ValidationIssue",
        "load_generation_template",
    }:
        from shared.rules.llm_dsl_generator import (
            LLMRuleGeneratorWorkflow,
            RuleCandidate,
            RuleGenerationRequest,
            RuleGenerationSession,
            RuleValidationResult,
            ValidationIssue,
            load_generation_template,
        )

        return {
            "LLMRuleGeneratorWorkflow": LLMRuleGeneratorWorkflow,
            "RuleCandidate": RuleCandidate,
            "RuleGenerationRequest": RuleGenerationRequest,
            "RuleGenerationSession": RuleGenerationSession,
            "RuleValidationResult": RuleValidationResult,
            "ValidationIssue": ValidationIssue,
            "load_generation_template": load_generation_template,
        }[name]
    if name in {"load_policy", "load_rules_dir", "load_rules_file"}:
        from shared.rules.loader import load_policy, load_rules_dir, load_rules_file

        return {
            "load_policy": load_policy,
            "load_rules_dir": load_rules_dir,
            "load_rules_file": load_rules_file,
        }[name]
    if name in {"MatchResult", "match_rules"}:
        from shared.rules.matcher import MatchResult, match_rules

        return {
            "MatchResult": MatchResult,
            "match_rules": match_rules,
        }[name]
    if name == "PolicySnapshot":
        from shared.rules.snapshot import PolicySnapshot

        return PolicySnapshot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
