"""Compatibility aliases for legacy checker imports."""
from __future__ import annotations

import importlib
import sys

from agentguard.plugins import (
    BaseChecker,
    CheckResult,
    CheckerManager,
    LLMInputChecker,
    LLMOutputChecker,
    ToolInvokeChecker,
    ToolResultChecker,
    checker_descriptions,
    default_checkers,
    get_checker_class,
    register,
    registered_checkers,
)

_ALIASES = (
    "base",
    "manager",
    "registry",
    "common",
    "common.patterns",
    "llm_before",
    "llm_before.llm_input",
    "llm_after",
    "llm_after.final_response",
    "llm_after.llm_output",
    "llm_after.llm_thought",
    "tool_before",
    "tool_before.tool_invoke",
    "tool_after",
    "tool_after.tool_result",
)

for alias in _ALIASES:
    sys.modules[f"{__name__}.{alias}"] = importlib.import_module(f"agentguard.plugins.{alias}")

__all__ = [
    "BaseChecker",
    "CheckResult",
    "CheckerManager",
    "default_checkers",
    "register",
    "get_checker_class",
    "registered_checkers",
    "checker_descriptions",
    "LLMInputChecker",
    "LLMOutputChecker",
    "ToolInvokeChecker",
    "ToolResultChecker",
]
