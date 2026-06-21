"""AutoGen agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError

_FUNC_ATTRS = ("func", "_func")


class AutogenAgentAdapter(BaseAgentAdapter):
    name = "autogen"

    def can_wrap(self, agent: Any) -> bool:
        return "autogen" in (type(agent).__module__ or "")

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        fn = getattr(agent, "generate_reply", None)
        if callable(fn):
            try:
                return fn(messages=messages)
            except Exception as exc:
                raise AdapterError(f"autogen generate_reply failed: {exc}") from exc
        raise AdapterError("autogen agent exposes no generate_reply")

    def getllm(self, agent: Any):
        model_client = getattr(agent, "_model_client", None)
        if model_client is None:
            return []
        methods: tuple[str, ...] = ("create", "create_stream")
        if type(model_client).__name__ == "BaseOpenAIChatCompletionClient":
            methods = (
                "_client.beta.chat.completions.parse",
                "_client.chat.completions.create",
                "_client.beta.chat.completions.stream",
            )
        elif type(model_client).__name__ == "BaseOllamaChatCompletionClient":
            methods = ("_client.chat",)
        elif type(model_client).__name__ == "BaseAnthropicChatCompletionClient":
            methods = ("_client.messages.create",)
        elif type(model_client).__name__ == "AzureAIChatCompletionClient":
            methods = ("_client.complete",)
        elif type(model_client).__name__ == "LlamaCppChatCompletionClient":
            methods = ("llm.create_chat_completion",)
        return self.collect_llm_methods(model_client, methods=methods)

    def gettools(self, agent: Any):
        bindings = []
        tools_list = getattr(agent, "_tools", None)
        if isinstance(tools_list, list):
            bindings.extend(self.collect_tool_list(tools_list, func_attrs=_FUNC_ATTRS))

        handoffs = getattr(agent, "_handoffs", None)
        if isinstance(handoffs, list):
            bindings.extend(self.collect_tool_list(handoffs, func_attrs=_FUNC_ATTRS))

        registry = getattr(agent, "function_map", None)
        if isinstance(registry, dict):
            bindings.extend(self.collect_function_map(registry))

        if hasattr(agent, "register_function"):
            bindings.extend(self.collect_register_function(agent))
        return bindings
