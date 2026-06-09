"""Server rule matcher (reuses client matcher for parity)."""
from __future__ import annotations

from agentguard.rules.matcher import MatchResult, match_rules

__all__ = ["match_rules", "MatchResult"]
