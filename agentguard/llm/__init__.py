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
from agentguard.llm.security_review import (
    COT_LEAK_DETECTOR,
    DEFAULT_PROMPT_DETECTORS,
    PROMPT_INJECTION_DETECTOR,
    PromptSecurityReviewer,
    PromptDetector,
    SecurityReviewRequest,
    SecurityReviewOrchestrator,
    SKILL_SAFETY_DETECTOR,
    TRACE_ANOMALY_DETECTOR,
    parse_security_review_response,
)
from agentguard.models.security_review import (
    SecurityReviewResult,
    ThreatFinding,
    ThreatSeverity,
    ThreatType,
)

__all__ = [
    "LLMBackend",
    "ChatResponse",
    "ToolCallRequest",
    "PromptSecurityReviewer",
    "PromptDetector",
    "SecurityReviewRequest",
    "SecurityReviewOrchestrator",
    "PROMPT_INJECTION_DETECTOR",
    "COT_LEAK_DETECTOR",
    "SKILL_SAFETY_DETECTOR",
    "TRACE_ANOMALY_DETECTOR",
    "DEFAULT_PROMPT_DETECTORS",
    "SecurityReviewResult",
    "ThreatFinding",
    "ThreatSeverity",
    "ThreatType",
    "parse_security_review_response",
]
