"""Tool registration, metadata, capabilities and degradation."""
from __future__ import annotations

from agentguard.tools import capability
from agentguard.tools.capability import ALL_CAPABILITIES, HIGH_RISK_CAPABILITIES, is_high_risk
from agentguard.tools.degrade import DegradePlan, ToolDegradeManager
from agentguard.tools.metadata import ToolMetadata
from agentguard.tools.registry import RegisteredTool, ToolRegistry
from agentguard.tools.wrapper import ToolWrapper

__all__ = [
    "capability",
    "ALL_CAPABILITIES",
    "HIGH_RISK_CAPABILITIES",
    "is_high_risk",
    "ToolMetadata",
    "ToolRegistry",
    "RegisteredTool",
    "ToolWrapper",
    "ToolDegradeManager",
    "DegradePlan",
]
