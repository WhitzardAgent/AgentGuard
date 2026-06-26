"""AutoGen agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent.normalization import LLMOutputNormalization
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

    def normalize_llm_output(
        self,
        *,
        label: str,
        output: Any,
        fn: Any = None,
        owner: Any = None,
    ) -> LLMOutputNormalization:
        _ = fn
        return LLMOutputNormalization(
            payload=_normalize_autogen_llm_output(self.normalize_value(output)),
            metadata=self._metadata(label=label, owner=owner),
        )

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


def _normalize_autogen_llm_output(value: Any) -> Any:
    extracted = _extract_autogen_llm_output_fields(value)
    return extracted if extracted is not None else value


def _extract_autogen_llm_output_fields(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    candidate = value
    for nested_key in ("message", "chat_message"):
        nested = value.get(nested_key)
        if isinstance(nested, dict):
            candidate = nested
            break

    if _is_autogen_structured_tool_call(candidate):
        return {"output": "", "final_output": None}
    if candidate is not value and _is_autogen_structured_tool_call(value):
        return {"output": "", "final_output": None}

    thought = _extract_autogen_thought(candidate)
    if thought is None and candidate is not value:
        thought = _extract_autogen_thought(value)

    visible = _extract_autogen_visible_text(candidate)
    if visible is None and candidate is not value:
        visible = _extract_autogen_visible_text(value)
    if visible is None:
        return None

    final_output = visible
    if thought is None:
        parsed = _parse_tagged_autogen_output(visible)
        thought = parsed.thought
        final_output = parsed.final_output

    payload = dict(value)
    payload["output"] = visible
    payload["final_output"] = final_output
    if thought is not None:
        payload["thought"] = thought
    return payload


def _extract_autogen_visible_text(value: dict[str, Any]) -> str | None:
    for key in ("content", "text", "output", "message"):
        text = _coerce_autogen_text(value.get(key))
        if text is not None:
            return text
    return None


def _extract_autogen_thought(value: dict[str, Any]) -> str | None:
    text = _coerce_autogen_text(value.get("thought"))
    if text is not None:
        return text

    for nested_key in ("metadata", "additional_kwargs"):
        nested = value.get(nested_key)
        if not isinstance(nested, dict):
            continue
        text = _coerce_autogen_text(nested.get("thought"))
        if text is not None:
            return text
        text = _coerce_autogen_text(nested.get("reasoning_content"))
        if text is not None:
            return text
    return None


def _coerce_autogen_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        parts = [item for item in value if isinstance(item, str) and item]
        if parts:
            return "\n\n".join(parts)
    return None


def _is_autogen_structured_tool_call(value: dict[str, Any]) -> bool:
    if isinstance(value.get("tool_calls"), list):
        return True
    if isinstance(value.get("function_call"), dict):
        return True

    content = value.get("content")
    if not isinstance(content, list) or not content:
        return False

    for item in content:
        if not isinstance(item, dict):
            return False
        if not (item.get("name") and ("arguments" in item or "args" in item or "parameters" in item)):
            return False
    return True


@dataclass(frozen=True)
class _ParsedAutogenOutput:
    thought: str | None
    final_output: str


_AUTOGEN_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_AUTOGEN_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_tagged_autogen_output(output: str) -> _ParsedAutogenOutput:
    thought_matches = list(_AUTOGEN_THOUGHT_TAG_RE.finditer(output))
    if not thought_matches:
        return _ParsedAutogenOutput(thought=None, final_output=output)

    thought_parts = [match.group("body").strip() for match in thought_matches]
    thought = "\n\n".join(part for part in thought_parts if part) or None
    remainder = _AUTOGEN_THOUGHT_TAG_RE.sub("", output).strip()

    final_matches = list(_AUTOGEN_FINAL_TAG_RE.finditer(remainder))
    if final_matches:
        final_parts = [match.group("body").strip() for match in final_matches]
        final_output = "\n\n".join(part for part in final_parts if part)
    else:
        final_output = remainder

    return _ParsedAutogenOutput(thought=thought, final_output=final_output)
