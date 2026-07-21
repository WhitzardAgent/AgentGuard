"""Agent adapters."""
from __future__ import annotations

from agentguard.adapters.agent.autogen import AutogenAgentAdapter
from agentguard.adapters.agent.base import BaseAgentAdapter, LLMBinding, ToolBinding
from agentguard.adapters.agent.crewai import CrewAIAgentAdapter
from agentguard.adapters.agent.custom import CustomAgentAdapter
from agentguard.adapters.agent.dify import install_dify_adapter
from agentguard.adapters.agent.dify_agent_chat import install_dify_agent_chat_adapter
from agentguard.adapters.agent.dify_bootstrap import install_dify_app_factory_capture
from agentguard.adapters.agent.langchain import LangChainAgentAdapter
from agentguard.adapters.agent.langgraph import LangGraphAgentAdapter
from agentguard.adapters.agent.llamaindex import LlamaIndexAgentAdapter
from agentguard.adapters.agent.metagpt import MetaGPTAgentAdapter
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
    "install_dify_adapter",
    "install_dify_agent_chat_adapter",
    "install_dify_app_factory_capture",
    "LangChainAgentAdapter",
    "LangGraphAgentAdapter",
    "LlamaIndexAgentAdapter",
    "MetaGPTAgentAdapter",
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
