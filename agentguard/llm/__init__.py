"""LLM backend abstraction for AgentGuard examples.

Priority:
  1. litellm  — if installed, use `litellm.completion(model=..., ...)`
  2. openai   — direct call with custom base_url (ZhipuAI, local Ollama, etc.)

Quick usage::

    from agentguard.llm import LLMBackend

    llm = LLMBackend.zhipuai(api_key="...", model="glm-4-flash")
    # or
    llm = LLMBackend.litellm("zai/glm-4-flash", api_key="...")
    # or any OpenAI-compatible endpoint
    llm = LLMBackend(model="gpt-4o", api_key="sk-...", base_url=None)
"""

from agentguard.llm.backend import LLMBackend, ChatResponse, ToolCallRequest

__all__ = ["LLMBackend", "ChatResponse", "ToolCallRequest"]
