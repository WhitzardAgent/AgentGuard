"""Tool registry, capability model and downgrade transforms."""

from agentguard.tools.capability import Capability, capabilities_for_sink
from agentguard.tools.downgrade import Downgrader
from agentguard.tools.metadata import ToolMetadata
from agentguard.tools.registry import RegisteredTool, ToolRegistry

__all__ = [
    "Capability",
    "capabilities_for_sink",
    "Downgrader",
    "ToolMetadata",
    "RegisteredTool",
    "ToolRegistry",
]
