"""Agent adapters."""
from __future__ import annotations

from agentguard.adapters.agent.autogen import AutogenAgentAdapter
from agentguard.adapters.agent.base import BaseAgentAdapter, LLMBinding, ToolBinding
from agentguard.adapters.agent.crewai import CrewAIAgentAdapter
from agentguard.adapters.agent.custom import CustomAgentAdapter
from agentguard.adapters.agent.langchain import LangChainAgentAdapter
from agentguard.adapters.agent.langgraph import LangGraphAgentAdapter
from agentguard.adapters.agent.llamaindex import LlamaIndexAgentAdapter
from agentguard.adapters.agent.normalization import (
    LLMInputNormalization,
    LLMOutputNormalization,
    ToolInvokeNormalization,
    ToolResultNormalization,
)
from agentguard.adapters.agent.openai_agents import OpenAIAgentsAdapter


__all__ = [
    "BaseAgentAdapter",
    "CustomAgentAdapter",
    "LangChainAgentAdapter",
    "LangGraphAgentAdapter",
    "LlamaIndexAgentAdapter",
    "AutogenAgentAdapter",
    "CrewAIAgentAdapter",
    "OpenAIAgentsAdapter",
    "ToolBinding",
    "LLMBinding",
    "LLMInputNormalization",
    "LLMOutputNormalization",
    "ToolInvokeNormalization",
    "ToolResultNormalization",
]
