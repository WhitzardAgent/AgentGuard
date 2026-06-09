"""Server-side checkers (reuse client checker manager for parity)."""
from __future__ import annotations

from agentguard.checkers.manager import CheckerManager


def server_checker_manager() -> CheckerManager:
    return CheckerManager()


__all__ = ["server_checker_manager", "CheckerManager"]
