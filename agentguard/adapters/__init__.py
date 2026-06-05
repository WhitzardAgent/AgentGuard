"""Framework adapters that normalize agents into a Harness-drivable step stream.

Each adapter knows how to turn a given LLM framework's run into a sequence of
:class:`AgentStep` values (thoughts, tool calls, final answers) that the
:class:`~agentguard.harness.GuardedAgent` drives under enforcement.

All third-party SDK imports are lazy and optional: adapters fall back to a
deterministic offline reasoning loop when the underlying library or credentials
are unavailable, so examples and tests run with no network or extra deps.
"""

from agentguard.adapters.anthropic import AnthropicAdapter
from agentguard.adapters.autogen import AutogenAdapter
from agentguard.adapters.base import AgentStep, BaseAdapter, StepKind
from agentguard.adapters.crewai import CrewAIAdapter
from agentguard.adapters.custom import CustomAdapter
from agentguard.adapters.langchain import LangChainAdapter
from agentguard.adapters.lite_llm import LiteLLMAdapter
from agentguard.adapters.openai_agents import OpenAIAdapter

__all__ = [
    "AgentStep",
    "BaseAdapter",
    "StepKind",
    "CustomAdapter",
    "OpenAIAdapter",
    "LiteLLMAdapter",
    "AnthropicAdapter",
    "LangChainAdapter",
    "AutogenAdapter",
    "CrewAIAdapter",
]
