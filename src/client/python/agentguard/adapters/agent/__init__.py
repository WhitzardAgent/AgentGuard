"""Agent adapters."""
from __future__ import annotations

from agentguard.adapters.agent.autogen import AutogenAgentAdapter
from agentguard.adapters.agent.base import (
    BaseAgentAdapter,
    GuardedAgent,
    select_agent_adapter,
)
from agentguard.adapters.agent.crewai import CrewAIAgentAdapter
from agentguard.adapters.agent.custom import CustomAgentAdapter
from agentguard.adapters.agent.langchain import LangChainAgentAdapter
from agentguard.adapters.agent.llamaindex import LlamaIndexAgentAdapter
from agentguard.adapters.agent.openai_agents import OpenAIAgentsAdapter


def default_agent_adapters() -> list[BaseAgentAdapter]:
    # Framework adapters first; custom is the catch-all fallback.
    return [
        LangChainAgentAdapter(),
        LlamaIndexAgentAdapter(),
        AutogenAgentAdapter(),
        CrewAIAgentAdapter(),
        OpenAIAgentsAdapter(),
        CustomAgentAdapter(),
    ]


__all__ = [
    "BaseAgentAdapter",
    "GuardedAgent",
    "select_agent_adapter",
    "CustomAgentAdapter",
    "LangChainAgentAdapter",
    "LlamaIndexAgentAdapter",
    "AutogenAgentAdapter",
    "CrewAIAgentAdapter",
    "OpenAIAgentsAdapter",
    "default_agent_adapters",
]
