"""Client-side Harness — the Policy Enforcement Point (PEP) runtime.

Wraps existing LLM agents and tools with minimal code changes, intercepts
runtime behaviours, normalizes them into events, and drives the PEP to enforce
decisions. Also hosts LLM thought management and the execution sandbox.
"""

from agentguard.harness.agent_wrapper import GuardedAgent
from agentguard.harness.event_bus import EventBus
from agentguard.harness.lifecycle import Lifecycle, LifecycleStage
from agentguard.harness.llm_thought_hook import LLMThoughtHook
from agentguard.harness.runtime_context import (
    current_context,
    push_context,
    use_context,
)
from agentguard.harness.sandbox import Sandbox, SandboxViolation
from agentguard.harness.tool_wrapper import ToolDenied, ToolWrapper

__all__ = [
    "GuardedAgent",
    "EventBus",
    "Lifecycle",
    "LifecycleStage",
    "LLMThoughtHook",
    "current_context",
    "push_context",
    "use_context",
    "Sandbox",
    "SandboxViolation",
    "ToolWrapper",
    "ToolDenied",
]
