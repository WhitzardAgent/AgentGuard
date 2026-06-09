"""Client-side rule loading and matching."""
from __future__ import annotations

from agentguard.rules.builtin import builtin_rules
from agentguard.rules.loader import load_policy, load_rules_dir, load_rules_file
from agentguard.rules.matcher import MatchResult, match_rules

__all__ = [
    "builtin_rules",
    "load_policy",
    "load_rules_dir",
    "load_rules_file",
    "MatchResult",
    "match_rules",
]
