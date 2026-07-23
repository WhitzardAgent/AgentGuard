"""Shared legacy Dify LLM guard helpers.

This module is intentionally limited to the old ``ModelInstance.invoke_llm``
path used by Dify workflow and legacy Agent Chat runtimes.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from typing import Any

from agentguard.adapters.agent.normalization import denormalize_llm_output_payload
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.utils.errors import AdapterError
from agentguard.utils.json import safe_dumps, safe_loads


@dataclass(frozen=True)
class DifyLegacyLLMCall:
    prompt_messages: Any = None
    model_parameters: Any = None
    tools: Any = None
    stop: Any = None
    stream: Any = True
    callbacks: Any = None

    @classmethod
    def from_args_kwargs(
        cls,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> DifyLegacyLLMCall:
        names = ["prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"]
        values = {name: kwargs.get(name) for name in names if name in kwargs}
        for index, value in enumerate(args):
            if index < len(names) and names[index] not in values:
                values[names[index]] = value
        values.setdefault("prompt_messages", [])
        values.setdefault("tools", None)
        values.setdefault("stream", True)
        return cls(**{name: values.get(name) for name in names})

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


GuardInput = Callable[[Any, DifyLegacyLLMCall, dict[str, Any] | None], GuardDecision]
GuardOutput = Callable[[Any, Any, DifyLegacyLLMCall, str | None, dict[str, Any] | None], GuardDecision]
BlockedValue = Callable[[GuardDecision], str | None]
Executor = Callable[[tuple[Any, ...], dict[str, Any]], Any]
ModifyOutput = Callable[[Any, Any], Any]


def run_dify_legacy_llm_call(
    *,
    model: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    arg_names: tuple[str, ...],
    execute: Executor,
    guard_input: GuardInput,
    guard_output: GuardOutput,
    blocked_value: BlockedValue,
    normalizer: DifyLegacyLLMNormalizer,
    modify_output: ModifyOutput | None = None,
) -> Any:
    current_args = tuple(args)
    current_kwargs = dict(kwargs)
    call = DifyLegacyLLMCall.from_args_kwargs(current_args, current_kwargs)
    decision = guard_input(model, call, None)
    if decision.decision_type == DecisionType.MODIFY_LLM_INPUT:
        current_args, current_kwargs = replace_named_argument(
            current_args,
            current_kwargs,
            arg_names,
            "prompt_messages",
            decision_payload(decision),
        )
        call = DifyLegacyLLMCall.from_args_kwargs(current_args, current_kwargs)
    blocked = blocked_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)
    try:
        result = execute(current_args, current_kwargs)
    except Exception as exc:
        guard_output(model, {"error": str(exc)}, call, str(exc), None)
        raise
    if is_generator_like(result):
        return _wrap_legacy_llm_generator(
            model=model,
            result=result,
            call=call,
            guard_output=guard_output,
            blocked_value=blocked_value,
            normalizer=normalizer,
        )
    decision = guard_output(model, result, call, None, None)
    if decision.decision_type == DecisionType.MODIFY_LLM_OUTPUT:
        output_modifier = modify_output or modified_llm_output_value
        result = output_modifier(decision_payload(decision), result)
    blocked = blocked_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)
    return result


def _wrap_legacy_llm_generator(
    *,
    model: Any,
    result: Any,
    call: DifyLegacyLLMCall,
    guard_output: GuardOutput,
    blocked_value: BlockedValue,
    normalizer: DifyLegacyLLMNormalizer,
) -> Generator[Any, None, None]:
    chunks: list[Any] = []
    try:
        for chunk in result:
            chunks.append(chunk)
            yield chunk
    except Exception as exc:
        guard_output(model, {"error": str(exc)}, call, str(exc), None)
        raise
    decision = guard_output(model, normalizer.stream_output_payload(chunks), call, None, None)
    blocked = blocked_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)


class DifyLegacyLLMNormalizer:
    def __init__(
        self,
        *,
        content_list_joiner: str = "\n",
        stream_text_joiner: str = "\n",
        preserve_dict_payload: bool = False,
        include_message_key: bool = False,
        include_text_data_attrs: bool = False,
        object_message_payload: bool = False,
        object_parts_payload: bool = False,
        include_stream_tool_calls: bool = False,
        value_attrs: tuple[str, ...] = ("role", "content", "name", "tool_name", "tool_call_id"),
    ) -> None:
        self.content_list_joiner = content_list_joiner
        self.stream_text_joiner = stream_text_joiner
        self.preserve_dict_payload = preserve_dict_payload
        self.include_message_key = include_message_key
        self.include_text_data_attrs = include_text_data_attrs
        self.object_message_payload = object_message_payload
        self.object_parts_payload = object_parts_payload
        self.include_stream_tool_calls = include_stream_tool_calls
        self.value_attrs = value_attrs

    def normalize_messages(self, messages: Any) -> list[dict[str, Any]]:
        if isinstance(messages, list):
            normalized: list[dict[str, Any]] = []
            for item in messages:
                if isinstance(item, dict):
                    normalized.append(
                        {
                            **item,
                            "role": str(item.get("role") or "user"),
                            "content": self.content_to_text(item.get("content")),
                        }
                    )
                else:
                    normalized.append(self.prompt_message_to_message(item))
            return normalized
        if messages is None:
            return []
        return [self.prompt_message_to_message(messages)]

    def prompt_message_to_message(self, message: Any) -> dict[str, Any]:
        role = self.message_role(message)
        content = get_attr_or_key(message, "content")
        data = self.normalize_value(message)
        if isinstance(data, dict):
            data.setdefault("role", role)
            data.setdefault("content", self.content_to_text(content))
            return data
        return {"role": role, "content": self.content_to_text(content)}

    def message_role(self, message: Any) -> str:
        name = type(message).__name__.lower()
        if "system" in name:
            return "system"
        if "assistant" in name:
            return "assistant"
        if "tool" in name:
            return "tool"
        return "user"

    def stream_output_payload(self, chunks: list[Any]) -> dict[str, Any]:
        text_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls: list[Any] = []
        for chunk in chunks:
            delta = get_attr_or_key(chunk, "delta")
            message = get_attr_or_key(delta, "message")
            content = get_attr_or_key(message, "content")
            if content is not None:
                text_parts.append(self.content_to_text(content))
            thought = self.extract_llm_thought(delta) or self.extract_llm_thought(message) or self.extract_llm_thought(chunk)
            if thought is not None:
                thought_parts.append(thought)
            calls = get_attr_or_key(message, "tool_calls")
            if self.include_stream_tool_calls and calls:
                tool_calls.extend(list(calls))
        output = self.stream_text_joiner.join(part for part in text_parts if part) or None
        payload = self.output_payload_from_text(
            output,
            thought="\n\n".join(thought_parts) or None,
        )
        if self.include_stream_tool_calls and tool_calls:
            payload["tool_calls"] = self.normalize_value(tool_calls)
        return payload

    def output_payload(self, output: Any) -> dict[str, Any]:
        if isinstance(output, dict):
            if "output" in output:
                text = self.content_to_optional_text(output.get("output"))
            elif "content" in output:
                text = self.content_to_optional_text(output.get("content"))
            elif "text" in output:
                text = self.content_to_optional_text(output.get("text"))
            elif self.include_message_key and "message" in output:
                text = self.content_to_optional_text(output.get("message"))
            elif output.get("tool_calls"):
                text = None
            else:
                text = self.content_to_text(output)
            payload = self.output_payload_from_text(
                text,
                thought=self.extract_llm_thought(output),
                final_output=self.content_to_optional_text(output.get("final_output"))
                if "final_output" in output
                else None,
            )
            if self.preserve_dict_payload:
                preserved = dict(output)
                preserved.update(payload)
                return preserved
            return payload

        if self.object_message_payload:
            message = get_attr_or_key(output, "message")
            if message is not None:
                content = get_attr_or_key(message, "content")
                tool_calls = get_attr_or_key(message, "tool_calls")
                payload = self.output_payload_from_text(
                    self.content_to_text(content),
                    thought=self.extract_llm_thought(output) or self.extract_llm_thought(message),
                )
                if tool_calls:
                    payload["tool_calls"] = self.normalize_value(tool_calls)
                return payload

        if self.object_parts_payload:
            parts = getattr(output, "parts", None)
            if isinstance(parts, list):
                text_parts: list[str] = []
                for part in parts:
                    content = getattr(part, "content", None)
                    if content is not None:
                        text_parts.append(self.content_to_text(content))
                text = "\n".join(part for part in text_parts if part) or None
                return self.output_payload_from_text(
                    text,
                    thought=self.extract_llm_thought({"parts": parts}),
                )

        return self.output_payload_from_text(
            self.content_to_text(output),
            thought=self.extract_llm_thought(output),
        )

    def output_payload_from_text(
        self,
        output: str | None,
        *,
        thought: str | None = None,
        final_output: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_tagged_llm_output(output) if output is not None else ParsedLLMOutput(None, None)
        payload = {
            "output": output,
            "final_output": final_output if final_output is not None else parsed.final_output,
        }
        thought = thought if thought is not None else parsed.thought
        if thought is not None:
            payload["thought"] = thought
        return payload

    def extract_llm_thought(self, value: Any) -> str | None:
        if isinstance(value, dict):
            direct = first_non_empty_text(
                self,
                value,
                "thought",
                "reasoning_content",
                "reasoningContent",
                "thinking",
                "reasoning",
            )
            if direct is not None:
                return direct
            for container_key in ("additional_kwargs", "response_metadata", "metadata", "extra"):
                nested = value.get(container_key)
                if isinstance(nested, dict):
                    nested_thought = self.extract_llm_thought(nested)
                    if nested_thought is not None:
                        return nested_thought
            for blocks_key in ("parts", "content", "output"):
                blocks = value.get(blocks_key)
                if isinstance(blocks, list):
                    block_thought = self.extract_llm_thought_from_blocks(blocks)
                    if block_thought is not None:
                        return block_thought
            return None

        direct = first_non_empty_attr(
            self,
            value,
            "thought",
            "reasoning_content",
            "reasoningContent",
            "thinking",
            "reasoning",
        )
        if direct is not None:
            return direct
        parts = getattr(value, "parts", None)
        if isinstance(parts, list):
            return self.extract_llm_thought_from_blocks(parts)
        return None

    def extract_llm_thought_from_blocks(self, blocks: list[Any]) -> str | None:
        thought_parts: list[str] = []
        for block in blocks:
            if isinstance(block, dict):
                block_type = str(block.get("type") or block.get("kind") or "").lower()
                if block_type in {"thinking", "thinkingblock", "reasoning", "reasoningblock"}:
                    text = first_non_empty_text(self, block, "content", "text", "thinking", "reasoning", "summary")
                    if text is not None:
                        thought_parts.append(text)
            else:
                block_type = str(getattr(block, "type", None) or getattr(block, "kind", None) or "").lower()
                if block_type in {"thinking", "thinkingblock", "reasoning", "reasoningblock"}:
                    text = first_non_empty_attr(self, block, "content", "text", "thinking", "reasoning", "summary")
                    if text is not None:
                        thought_parts.append(text)
        return "\n\n".join(thought_parts) or None

    def normalize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, bool | int | float | str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {str(key): self.normalize_value(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self.normalize_value(item) for item in value]
        for attr in ("model_dump", "to_dict", "dict"):
            dumper = getattr(value, attr, None)
            if callable(dumper):
                try:
                    return self.normalize_value(dumper())
                except Exception:
                    continue
        data: dict[str, Any] = {}
        for attr in self.value_attrs:
            item = getattr(value, attr, None)
            if item is not None:
                data[attr] = self.normalize_value(item)
        return data or str(value)

    def content_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, list | tuple):
            return self.content_list_joiner.join(self.content_to_text(item) for item in value)
        if isinstance(value, dict):
            return safe_dumps(value)
        if self.include_text_data_attrs:
            text = getattr(value, "text", None)
            if text is not None:
                return self.content_to_text(text)
            data = getattr(value, "data", None)
            if data is not None:
                return self.content_to_text(data)
        return str(value)

    def content_to_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = self.content_to_text(value)
        return text if text else None


@dataclass(frozen=True)
class ParsedLLMOutput:
    thought: str | None
    final_output: str | None


_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def parse_tagged_llm_output(output: str) -> ParsedLLMOutput:
    thought_matches = list(_THOUGHT_TAG_RE.finditer(output))
    if not thought_matches:
        return ParsedLLMOutput(thought=None, final_output=output)

    thought_parts = [match.group("body").strip() for match in thought_matches]
    thought = "\n\n".join(part for part in thought_parts if part) or None
    remainder = _THOUGHT_TAG_RE.sub("", output).strip()

    final_matches = list(_FINAL_TAG_RE.finditer(remainder))
    if final_matches:
        final_parts = [match.group("body").strip() for match in final_matches]
        final_output = "\n\n".join(part for part in final_parts if part)
    else:
        final_output = remainder

    return ParsedLLMOutput(thought=thought, final_output=final_output)


def first_non_empty_text(
    normalizer: DifyLegacyLLMNormalizer,
    value: dict[str, Any],
    *keys: str,
) -> str | None:
    for key in keys:
        text = normalizer.content_to_optional_text(value.get(key))
        if text is not None:
            return text
    return None


def first_non_empty_attr(
    normalizer: DifyLegacyLLMNormalizer,
    value: Any,
    *keys: str,
) -> str | None:
    for key in keys:
        text = normalizer.content_to_optional_text(getattr(value, key, None))
        if text is not None:
            return text
    return None


def get_attr_or_key(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def is_generator_like(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, str | bytes | dict | list | tuple)


def decision_payload(decision: GuardDecision) -> Any:
    payload = decision.processed_content
    if not isinstance(payload, str):
        return payload
    text = payload.strip()
    if not text or text[0] not in {"{", "["}:
        return payload
    return safe_loads(text, fallback=payload)


def replace_named_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    names: tuple[str, ...],
    key: str,
    value: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    current_args = list(args)
    current_kwargs = dict(kwargs)
    if key in current_kwargs:
        current_kwargs[key] = value
        return tuple(current_args), current_kwargs

    try:
        index = names.index(key)
    except ValueError:
        current_kwargs[key] = value
        return tuple(current_args), current_kwargs

    if index < len(current_args):
        current_args[index] = value
    else:
        current_kwargs[key] = value
    return tuple(current_args), current_kwargs


def modified_llm_output_value(payload: Any, result: Any) -> Any:
    return denormalize_llm_output_payload(payload=payload, output=result)


def modified_message_llm_output_value(payload: Any, result: Any) -> Any:
    message = get_attr_or_key(result, "message")
    if message is not None and hasattr(message, "content"):
        updated_result = copy.copy(result)
        updated_message = copy.copy(message)
        updated_message.content = denormalize_llm_output_payload(
            payload=payload,
            output=getattr(message, "content", None),
        )
        updated_result.message = updated_message
        return updated_result
    return denormalize_llm_output_payload(payload=payload, output=result)


__all__ = [
    "DifyLegacyLLMCall",
    "DifyLegacyLLMNormalizer",
    "ParsedLLMOutput",
    "decision_payload",
    "get_attr_or_key",
    "is_generator_like",
    "modified_llm_output_value",
    "modified_message_llm_output_value",
    "parse_tagged_llm_output",
    "replace_named_argument",
    "run_dify_legacy_llm_call",
]
