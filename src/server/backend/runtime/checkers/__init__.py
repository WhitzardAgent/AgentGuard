"""Server-side checkers kept in parity with the client checker layout."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.manager import CheckerManager


def server_checker_manager(config: str | Path | dict[str, Any] | None = None) -> CheckerManager:
    return CheckerManager(config=config)


__all__ = ["server_checker_manager", "CheckerManager", "BaseChecker", "CheckResult"]
