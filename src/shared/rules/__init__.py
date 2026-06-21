"""Shared rule loading, matching and snapshot helpers."""
from __future__ import annotations

from shared.rules.builtin import builtin_rules
from shared.rules.loader import load_policy, load_rules_dir, load_rules_file
from shared.rules.matcher import MatchResult, match_rules
from shared.rules.snapshot import PolicySnapshot

__all__ = [
    "builtin_rules",
    "load_policy",
    "load_rules_dir",
    "load_rules_file",
    "MatchResult",
    "match_rules",
    "PolicySnapshot",
]
