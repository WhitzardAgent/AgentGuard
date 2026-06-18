"""AutoGen agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent.patching import (
    is_guarded,
    make_guarded_tool,
    mark_patched,
    patch_llm_methods,
    set_attr,
    tool_name,
)
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

    def patchLLM(self, agent: Any, guard: Any) -> int:
        patched = 0
        model_client = getattr(agent, "_model_client", None)
        if model_client is None:
            return 0
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
        patched += patch_llm_methods(guard, model_client, methods=methods)
        return patched

    def patchtool(self, agent: Any, guard: Any) -> int:
        patched = 0
        tools_list = getattr(agent, "_tools", None)
        if isinstance(tools_list, list):
            patched += self._patch_tools_list(tools_list, guard)

        handoffs = getattr(agent, "_handoffs", None)
        if isinstance(handoffs, list):
            patched += self._patch_tools_list(handoffs, guard)

        registry = getattr(agent, "function_map", None)
        if isinstance(registry, dict):
            patched += self._patch_function_map(registry, guard)

        if hasattr(agent, "register_function"):
            patched += self._patch_register_function(agent, guard)
        return patched

    def _patch_tools_list(self, tools_list: list[Any], guard: Any) -> int:
        patched = 0
        for idx, tool in enumerate(tools_list):
            if is_guarded(tool):
                continue

            fn, attr = _extract_tool_fn(tool)
            if fn is not None and attr is not None:
                name = tool_name(tool, fn, fallback=f"tool_{idx}")
                wrapped = make_guarded_tool(guard, fn, name=name, tool=tool)
                if set_attr(tool, attr, wrapped):
                    mark_patched(tool)
                else:
                    tools_list[idx] = wrapped
                patched += 1
                continue

            run_json = getattr(tool, "run_json", None)
            if callable(run_json) and not is_guarded(run_json):
                name = tool_name(tool, run_json, fallback=f"tool_{idx}")
                wrapped = make_guarded_tool(guard, run_json, name=name, tool=tool)
                if set_attr(tool, "run_json", wrapped):
                    mark_patched(tool)
                    patched += 1
                continue

            if callable(tool):
                name = tool_name(tool, fallback=f"tool_{idx}")
                tools_list[idx] = make_guarded_tool(guard, tool, name=name, tool=tool)
                patched += 1
        return patched

    def _patch_function_map(self, registry: dict[str, Any], guard: Any) -> int:
        patched = 0
        for name, fn in list(registry.items()):
            if not callable(fn) or is_guarded(fn):
                continue
            registry[name] = make_guarded_tool(guard, fn, name=name, tool=fn)
            patched += 1
        return patched

    def _patch_register_function(self, agent: Any, guard: Any) -> int:
        original = getattr(agent, "register_function", None)
        if not callable(original) or is_guarded(original):
            return 0

        def patched(func: Any = None, /, **kwargs: Any) -> Any:
            if callable(func) and not is_guarded(func):
                name = kwargs.get("name") or tool_name(func)
                func = make_guarded_tool(guard, func, name=name, tool=func)
            return original(func, **kwargs)

        set_attr(agent, "register_function", patched)
        return 1


def _extract_tool_fn(tool: Any) -> tuple[Any, str | None]:
    for attr in _FUNC_ATTRS:
        fn = getattr(tool, attr, None)
        if callable(fn) and not is_guarded(fn):
            return fn, attr
    return None, None
