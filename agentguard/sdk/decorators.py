"""Decorator-based tool registration API.

Usage:
    guard = Guard(...)

    @guard.tool("shell.exec", sink_type="shell")
    def shell_exec(cmd: str) -> str:
        ...
"""

from __future__ import annotations

# The decorator API is provided directly by Guard.tool() in guard.py.
# This module exists as an extension point for additional decorators.
