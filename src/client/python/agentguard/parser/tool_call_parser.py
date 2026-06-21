"""Parse tool calls from many provider formats into ToolCall objects."""
from __future__ import annotations

import json
import re
from typing import Any

from agentguard.parser.function_call_parser import _coerce_args, parse_function_call
from agentguard.schemas.tool import ParseResult, ToolCall

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_tool_calls(output: Any) -> ParseResult:
    """Best-effort parse of LLM output into normalized tool calls."""
    result = ParseResult()
    if output is None:
        return result

    if isinstance(output, dict):
        _parse_dict(output, result)
        return result

    if isinstance(output, list):
        for item in output:
            sub = parse_tool_calls(item)
            result.tool_calls.extend(sub.tool_calls)
            result.errors.extend(sub.errors)
            result.malformed = result.malformed or sub.malformed
        return result

    if isinstance(output, str):
        _parse_string(output, result)
        return result

    result.errors.append(f"unsupported output type: {type(output).__name__}")
    return result


def _parse_dict(obj: dict[str, Any], result: ParseResult) -> None:
    # OpenAI tool_calls list
    if "tool_calls" in obj and isinstance(obj["tool_calls"], list):
        for tc in obj["tool_calls"]:
            call = _parse_openai_tool_call(tc)
            if call:
                result.tool_calls.append(call)
            else:
                result.malformed = True
                result.errors.append("malformed openai tool_call")
        return

    # OpenAI legacy function_call
    if "function_call" in obj:
        call = parse_function_call(obj)
        if call:
            result.tool_calls.append(call)
        else:
            result.malformed = True
        return

    # Anthropic tool_use
    if obj.get("type") == "tool_use":
        result.tool_calls.append(
            ToolCall(
                tool_name=obj.get("name", ""),
                arguments=obj.get("input") or {},
                call_id=obj.get("id"),
                raw=obj,
                source_format="anthropic_tool_use",
            )
        )
        return

    # Plain dict tool call: {"tool"/"name": ..., "arguments"/"args"/"parameters": {...}}
    name = obj.get("tool") or obj.get("name") or obj.get("tool_name")
    if name:
        args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
        result.tool_calls.append(
            ToolCall(
                tool_name=name,
                arguments=_coerce_args(args),
                call_id=obj.get("id"),
                raw=obj,
                source_format="plain_dict",
            )
        )
        return

    result.errors.append("no tool call found in dict")


def _parse_openai_tool_call(tc: dict[str, Any]) -> ToolCall | None:
    fn = tc.get("function") or {}
    name = fn.get("name") or tc.get("name")
    if not name:
        return None
    return ToolCall(
        tool_name=name,
        arguments=_coerce_args(fn.get("arguments", tc.get("arguments"))),
        call_id=tc.get("id"),
        raw=tc,
        source_format="openai_tool_call",
    )


def _parse_string(text: str, result: ParseResult) -> None:
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        result.errors.append("no JSON object in string output")
        return
    blob = match.group(0)
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        result.malformed = True
        result.errors.append("malformed JSON tool call")
        return
    if isinstance(obj, dict):
        _parse_dict(obj, result)
