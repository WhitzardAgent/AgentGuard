"""LLM adapters."""
from __future__ import annotations

from agentguard.adapters.llm.anthropic import AnthropicLLMAdapter
from agentguard.adapters.llm.base import BaseLLMAdapter, GuardedLLM, select_llm_adapter
from agentguard.adapters.llm.custom import CustomLLMAdapter
from agentguard.adapters.llm.gemini import GeminiLLMAdapter
from agentguard.adapters.llm.litellm import LiteLLMAdapter
from agentguard.adapters.llm.openai import OpenAILLMAdapter
from agentguard.adapters.llm.vllm import VLLMAdapter


def default_llm_adapters() -> list[BaseLLMAdapter]:
    return [
        OpenAILLMAdapter(),
        AnthropicLLMAdapter(),
        LiteLLMAdapter(),
        GeminiLLMAdapter(),
        VLLMAdapter(),
        CustomLLMAdapter(),
    ]


__all__ = [
    "BaseLLMAdapter",
    "GuardedLLM",
    "select_llm_adapter",
    "CustomLLMAdapter",
    "OpenAILLMAdapter",
    "AnthropicLLMAdapter",
    "LiteLLMAdapter",
    "GeminiLLMAdapter",
    "VLLMAdapter",
    "default_llm_adapters",
]
